"""
Parser that produces `structures.ServicePackage`.
"""

from collections.abc import Iterable

from boto3.session import Session
from botocore import xform_name

from mypy_boto3_builder.logger import get_logger
from mypy_boto3_builder.package_data import BasePackageData
from mypy_boto3_builder.parsers.client import parse_client
from mypy_boto3_builder.parsers.service_resource import parse_service_resource
from mypy_boto3_builder.parsers.shape_parser import ShapeParser
from mypy_boto3_builder.service_name import ServiceName
from mypy_boto3_builder.structures.client import Client
from mypy_boto3_builder.structures.method import Method
from mypy_boto3_builder.structures.paginator import Paginator
from mypy_boto3_builder.structures.service_package import ServicePackage
from mypy_boto3_builder.structures.waiter import Waiter
from mypy_boto3_builder.type_annotations.type_def_sortable import TypeDefSortable
from mypy_boto3_builder.utils.type_def_sorter import TypeDefSorter


class ServicePackageParser:
    """
    Parser that produces `structures.ServicePackage`.

    Arguments:
        session -- boto3 session.
        service_name -- Target service name.
        package_data -- Package data.

    Returns:
        ServiceModule structure.
    """

    def __init__(
        self,
        session: Session,
        service_name: ServiceName,
        package_data: type[BasePackageData],
    ) -> None:
        self.session = session
        self.service_name = service_name
        self.package_data = package_data
        self.shape_parser = ShapeParser(self.session, self.service_name)
        self._logger = get_logger()

    def parse(self) -> ServicePackage:
        """
        Extract all data from boto3 service package.
        """
        result = self._parse_service_package()
        result.waiters.extend(self._parse_waiters(result.client))
        result.paginators.extend(self._parse_paginators(result.client))
        result.client.methods.extend(
            self._get_extra_client_methods(result.paginators, result.waiters)
        )

        self.shape_parser.fix_typed_dict_names()
        self.shape_parser.fix_method_arguments_for_mypy(
            [
                *result.client.methods,
                *(result.service_resource.methods if result.service_resource else []),
                *[method for paginator in result.paginators for method in paginator.methods],
                *[method for waiter in result.waiters for method in waiter.methods],
            ]
        )
        result.type_defs = self._get_type_defs(result.get_type_defs())
        result.literals = result.extract_literals()
        result.validate()

        return result

    def _parse_service_package(self) -> ServicePackage:
        client = parse_client(self.session, self.service_name, self.shape_parser)
        service_resource = parse_service_resource(
            self.session, self.service_name, self.shape_parser
        )

        return ServicePackage(
            data=self.package_data,
            service_name=self.service_name,
            client=client,
            service_resource=service_resource,
        )

    def _parse_waiters(self, client: Client) -> list[Waiter]:
        waiters: list[Waiter] = []
        waiter_names: list[str] = client.boto3_client.waiter_names
        for waiter_name in waiter_names:
            self._logger.debug(f"Parsing Waiter {waiter_name}")
            waiter = client.boto3_client.get_waiter(waiter_name)
            waiter_record = Waiter(
                name=f"{waiter.name}Waiter",
                waiter_name=waiter_name,
                service_name=self.service_name,
            )

            wait_method = self.shape_parser.get_wait_method(waiter.name)
            waiter_record.methods.append(wait_method)
            waiters.append(waiter_record)

        return waiters

    def _parse_paginators(self, client: Client) -> list[Paginator]:
        result: list[Paginator] = []
        for paginator_name in self.shape_parser.get_paginator_names():
            self._logger.debug(f"Parsing Paginator {paginator_name}")
            operation_name = xform_name(paginator_name)
            # boto3_paginator = client.boto3_client.get_paginator(operation_name)
            paginator_record = Paginator(
                name=f"{paginator_name}Paginator",
                paginator_name=paginator_name,
                operation_name=operation_name,
                service_name=self.service_name,
            )

            paginate_method = self.shape_parser.get_paginate_method(paginator_name)
            paginator_record.methods.append(paginate_method)
            result.append(paginator_record)

        return result

    def _get_extra_client_methods(
        self, paginators: list[Paginator], waiters: list[Waiter]
    ) -> list[Method]:
        result: list[Method] = []
        for paginator in paginators:
            method = paginator.get_client_method()
            if len(paginators) == 1:
                method.decorators.clear()
            result.append(method)

        for waiter in waiters:
            method = waiter.get_client_method()
            if len(waiters) == 1:
                method.decorators.clear()
            result.append(method)

        return result

    def _get_type_defs(self, type_defs: Iterable[TypeDefSortable]) -> list[TypeDefSortable]:
        type_def_sorter = TypeDefSorter(type_defs)
        result = type_def_sorter.sort()
        return result
