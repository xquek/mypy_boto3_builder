"""
Parser for botocore shape files.
"""
import contextlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from boto3.resources.model import Collection
from boto3.session import Session
from botocore import xform_name
from botocore.eventstream import EventStream
from botocore.exceptions import UnknownServiceError
from botocore.model import (
    ListShape,
    MapShape,
    OperationModel,
    ServiceModel,
    Shape,
    StringShape,
    StructureShape,
)
from botocore.session import Session as BotocoreSession

from mypy_boto3_builder.logger import get_logger
from mypy_boto3_builder.service_name import ServiceName
from mypy_boto3_builder.structures.argument import Argument
from mypy_boto3_builder.structures.method import Method
from mypy_boto3_builder.type_annotations.external_import import ExternalImport
from mypy_boto3_builder.type_annotations.fake_annotation import FakeAnnotation
from mypy_boto3_builder.type_annotations.internal_import import AliasInternalImport, InternalImport
from mypy_boto3_builder.type_annotations.type import Type
from mypy_boto3_builder.type_annotations.type_constant import TypeConstant
from mypy_boto3_builder.type_annotations.type_literal import TypeLiteral
from mypy_boto3_builder.type_annotations.type_subscript import TypeSubscript
from mypy_boto3_builder.type_annotations.type_typed_dict import TypeTypedDict
from mypy_boto3_builder.type_annotations.type_union import TypeUnion
from mypy_boto3_builder.type_maps.argument_alias_map import get_argument_alias
from mypy_boto3_builder.type_maps.literal_type_map import get_literal_type_stub
from mypy_boto3_builder.type_maps.method_type_map import (
    get_default_value_stub,
    get_method_type_stub,
)
from mypy_boto3_builder.type_maps.shape_type_map import (
    OUTPUT_SHAPE_TYPE_MAP,
    SHAPE_TYPE_MAP,
    get_shape_type_stub,
)
from mypy_boto3_builder.type_maps.typed_dicts import (
    EmptyResponseMetadataTypeDef,
    PaginatorConfigTypeDef,
    ResponseMetadataTypeDef,
    WaiterConfigTypeDef,
)
from mypy_boto3_builder.utils.boto3_utils import get_botocore_session
from mypy_boto3_builder.utils.strings import get_type_def_name
from mypy_boto3_builder.utils.type_def_sorter import TypeDefSorter


class ShapeParserError(Exception):
    """
    Main error for ShapeParser.
    """


class ShapeParser:
    """
    Parser for botocore shape files.

    Arguments:
        session -- Boto3 session.
        service_name -- ServiceName.
    """

    def __init__(self, session: Session, service_name: ServiceName):
        loader = session._loader
        botocore_session: BotocoreSession = get_botocore_session(session)
        service_data = botocore_session.get_service_data(service_name.boto3_name)
        self.service_name = service_name
        self.service_model = ServiceModel(service_data, service_name.boto3_name)
        self._resource_name: str = ""
        self._type_literal_map: dict[str, TypeLiteral] = {}
        self._typed_dict_map: dict[str, TypeTypedDict] = {}
        self._output_typed_dict_map: dict[str, TypeTypedDict] = {}
        self._response_typed_dict_map: dict[str, TypeTypedDict] = {}
        self._fixed_typed_dict_map: dict[TypeTypedDict, TypeTypedDict] = {}

        self._waiters_shape: Mapping[str, Any] | None = None
        with contextlib.suppress(UnknownServiceError):
            self._waiters_shape = loader.load_service_model(service_name.boto3_name, "waiters-2")

        self._paginators_shape: Mapping[str, Any] | None = None
        with contextlib.suppress(UnknownServiceError):
            self._paginators_shape = loader.load_service_model(
                service_name.boto3_name, "paginators-1"
            )

        self._resources_shape: Mapping[str, Any] | None = None
        with contextlib.suppress(UnknownServiceError):
            self._resources_shape = loader.load_service_model(
                service_name.boto3_name, "resources-1"
            )

        self.logger = get_logger()

    @property
    def resource_name(self) -> str:
        """
        Parsed resource name.
        """
        return self._resource_name

    def _get_operation(self, name: str) -> OperationModel:
        return self.service_model.operation_model(name)

    def _get_operation_names(self) -> list[str]:
        return list(self.service_model.operation_names)

    def _get_paginator(self, name: str) -> dict[str, Any]:
        if not self._paginators_shape:
            raise ShapeParserError(f"Unknown paginator: {name}")
        try:
            return self._paginators_shape["pagination"][name]
        except KeyError as e:
            raise ShapeParserError(f"Unknown paginator: {name}") from e

    def _get_service_resource(self) -> dict[str, Any]:
        if not self._resources_shape:
            raise ShapeParserError("Resource shape not found")
        return self._resources_shape["service"]

    def _get_resource_names(self) -> list[str]:
        if not self._resources_shape:
            return []
        if "resources" not in self._resources_shape:
            return []

        return list(self._resources_shape["resources"].keys())

    def _get_resource_shape(self, name: str) -> dict[str, Any]:
        if not self._resources_shape:
            raise ShapeParserError("Resource shape not found")
        try:
            return self._resources_shape["resources"][name]
        except KeyError as e:
            raise ShapeParserError(f"Unknown resource: {name}") from e

    def get_paginator_names(self) -> list[str]:
        """
        Get available paginator names.

        Returns:
            A list of paginator names.
        """
        result: list[str] = []
        if self._paginators_shape:
            for name in self._paginators_shape.get("pagination", []):
                result.append(name)
        result.sort()
        return result

    def _parse_arguments(
        self,
        class_name: str,
        method_name: str,
        operation_name: str,
        shape: StructureShape,
        exclude_names: Iterable[str] = (),
        optional_only: bool = False,
    ) -> list[Argument]:
        result: list[Argument] = []
        required = shape.required_members
        for argument_name, argument_shape in shape.members.items():
            if argument_name in exclude_names:
                continue
            argument_alias = get_argument_alias(self.service_name, operation_name, argument_name)
            if argument_alias is None:
                continue

            argument_type_stub = get_method_type_stub(
                self.service_name, class_name, method_name, argument_name
            )
            if argument_type_stub is Type.RemoveArgument:
                continue
            if argument_type_stub is not None:
                argument_type = argument_type_stub
            else:
                argument_type = self.parse_shape(argument_shape)
            argument = Argument(argument_alias, argument_type)
            if argument_name not in required:
                argument.default = Type.Ellipsis
            default_value_stub = get_default_value_stub(
                self.service_name, class_name, method_name, argument_name
            )
            if default_value_stub is not None:
                argument.default = default_value_stub
            if optional_only and argument.required:
                continue

            # FIXME: https://github.com/boto/boto3/issues/2813
            # if not argument.required and argument.type_annotation:
            #     argument.type_annotation = Type.get_optional(argument.type_annotation)

            result.append(argument)

        result.sort(key=lambda x: not x.required)
        return result

    def _parse_return_type(
        self, class_name: str, method_name: str, shape: Shape | None
    ) -> FakeAnnotation:
        argument_type_stub = get_method_type_stub(
            self.service_name, class_name, method_name, "return"
        )
        if argument_type_stub is not None:
            return argument_type_stub

        if shape:
            return self.parse_shape(shape, output=True)

        return Type.none

    @staticmethod
    def _get_kw_flags(method_name: str, arguments: Sequence[Argument]) -> list[Argument]:
        if len(arguments) and not method_name[0].isupper():
            return [Argument.kwflag()]

        return []

    def get_client_method_map(self) -> dict[str, Method]:
        """
        Get client methods from shape.

        Returns:
            A map of method name to Method.
        """
        self._resource_name = "Client"
        result: dict[str, Method] = {
            "can_paginate": Method(
                "can_paginate",
                [Argument("self", None), Argument("operation_name", Type.str)],
                Type.bool,
            ),
            "generate_presigned_url": Method(
                "generate_presigned_url",
                [
                    Argument("self", None),
                    Argument("ClientMethod", Type.str),
                    Argument("Params", Type.MappingStrAny, Type.Ellipsis),
                    Argument("ExpiresIn", Type.int, TypeConstant(3600)),
                    Argument("HttpMethod", Type.str, Type.Ellipsis),
                ],
                Type.str,
            ),
            "close": Method(
                "close",
                [Argument("self", None)],
                Type.none,
            ),
        }
        for operation_name in self._get_operation_names():
            operation_model = self._get_operation(operation_name)
            arguments: list[Argument] = [Argument("self", None)]
            method_name = xform_name(operation_name)

            if operation_model.input_shape is not None:
                shape_arguments = self._parse_arguments(
                    "Client",
                    method_name,
                    operation_name,
                    operation_model.input_shape,
                )
                arguments.extend(self._get_kw_flags(method_name, shape_arguments))
                arguments.extend(shape_arguments)

            return_type = self._parse_return_type(
                "Client", method_name, operation_model.output_shape
            )
            if return_type is Type.none:
                return_type = EmptyResponseMetadataTypeDef

            method = Method(name=method_name, arguments=arguments, return_type=return_type)
            if operation_model.input_shape:
                method.create_request_type_annotation(
                    self._get_typed_dict_name(operation_model.input_shape, postfix="Request")
                )
            result[method.name] = method
        return result

    @staticmethod
    def _get_typed_dict_name(shape: Shape, postfix: str = "") -> str:
        return get_type_def_name(shape.name, postfix)

    @staticmethod
    def _get_literal_name(shape: StringShape) -> str:
        # FIXME: hack for APIGWv2
        if shape.name == "__string":
            children_name = "".join(sorted(f"{i[0].upper()}{i[1:]}" for i in shape.enum))
            return f"{children_name}Type"

        name = shape.name.lstrip("_")
        name = f"{name}Type"
        return name

    def _parse_shape_string(self, shape: StringShape, output_child: bool) -> FakeAnnotation:
        if shape.enum:
            children = list(shape.enum)
            literal_name = self._get_literal_name(shape)
            literal_type_stub = get_literal_type_stub(self.service_name, literal_name)
            children = literal_type_stub if literal_type_stub else list(shape.enum)
            type_literal = TypeLiteral(literal_name, children)
            if literal_name in self._type_literal_map:
                old_type_literal = self._type_literal_map[literal_name]
                if not type_literal.is_same(old_type_literal):
                    raise ValueError(
                        f"Literal {literal_name} has different values:"
                        f" {old_type_literal.children} vs {type_literal.children}"
                    )
                return old_type_literal

            self._type_literal_map[literal_name] = type_literal
            return type_literal

        # FIXME: botocore does not always try to parse response as JSON
        # pattern = shape.metadata.get("pattern", "")
        # if pattern in (
        #     "[\\u0009\\u000A\\u000D\\u0020-\\u00FF]+",
        #     "^[\\u0009\\u000A\\u000D\\u0020-\\u00FF]+$",
        # ):
        #     if output_child:
        #         return Type.DictStrAny
        #     else:
        #         return DictOrStrTypeDef

        return Type.str

    def _parse_shape_map(
        self,
        shape: MapShape,
        output_child: bool = False,
        is_streaming: bool = False,
    ) -> FakeAnnotation:
        type_subscript = TypeSubscript(Type.Dict) if output_child else TypeSubscript(Type.Mapping)
        if shape.key:
            type_subscript.add_child(
                self.parse_shape(shape.key, output_child=output_child, is_streaming=is_streaming)
            )
        else:
            type_subscript.add_child(Type.str)
        if shape.value:
            type_subscript.add_child(
                self.parse_shape(shape.value, output_child=output_child, is_streaming=is_streaming)
            )
        else:
            type_subscript.add_child(Type.Any)
        return type_subscript

    def _get_typed_dict_map(self, output: bool, output_child: bool) -> dict[str, TypeTypedDict]:
        if output:
            return self._response_typed_dict_map
        if output_child:
            self._output_typed_dict_map
        return self._typed_dict_map

    def _parse_shape_structure(
        self,
        shape: StructureShape,
        output: bool = False,
        output_child: bool = False,
        is_streaming: bool = False,
    ) -> FakeAnnotation:
        if not shape.members.items():
            return Type.DictStrAny if output_child else Type.MappingStrAny

        required = shape.required_members
        is_output_or_child = output or output_child
        typed_dict_name = self._get_shape_type_name(shape)
        typed_dict = TypeTypedDict(typed_dict_name)

        typed_dict_map = self._get_typed_dict_map(output, output_child)
        resource_typed_dict_name = self._get_typed_dict_name(shape, postfix=self.resource_name)
        found_typed_dict = typed_dict_map.get(typed_dict.name)
        found_resource_typed_dict = typed_dict_map.get(resource_typed_dict_name)

        if found_resource_typed_dict:
            return found_resource_typed_dict

        typed_dict_map[resource_typed_dict_name] = typed_dict

        for attr_name, attr_shape in shape.members.items():
            typed_dict.add_attribute(
                attr_name,
                self.parse_shape(
                    attr_shape,
                    output=False,
                    output_child=is_output_or_child,
                    is_streaming=is_streaming,
                ),
                attr_name in required,
            )
        if output:
            self._mark_typed_dict_as_total(typed_dict)
            self._add_response_metadata(typed_dict)

        if found_typed_dict and not typed_dict.is_same(found_typed_dict):
            self.logger.debug(
                f"Renaming conflicting {typed_dict.name} to {resource_typed_dict_name}"
            )
            typed_dict.name = resource_typed_dict_name
        typed_dict_map[typed_dict.name] = typed_dict
        return typed_dict

    def _mark_typed_dict_as_total(self, typed_dict: TypeTypedDict) -> None:
        for attribute in typed_dict.children:
            attribute.required = True

    def _add_response_metadata(self, typed_dict: TypeTypedDict) -> None:
        child_names = {i.name for i in typed_dict.children}
        if "ResponseMetadata" not in child_names:
            typed_dict.add_attribute(
                "ResponseMetadata",
                ResponseMetadataTypeDef,
                True,
            )

    def _parse_shape_list(self, shape: ListShape, output_child: bool = False) -> FakeAnnotation:
        type_subscript = TypeSubscript(Type.List) if output_child else TypeSubscript(Type.Sequence)
        if shape.member:
            type_subscript.add_child(self.parse_shape(shape.member, output_child=output_child))
        else:
            type_subscript.add_child(Type.Any)
        return type_subscript

    def _get_shape_type_name(self, shape: Shape) -> str:
        if isinstance(shape, StructureShape):
            return self._get_typed_dict_name(shape)

        if isinstance(shape, StringShape):
            return shape.name

        return shape.type_name

    @staticmethod
    def _get_streaming_body(shape: Shape) -> Shape | None:
        """
        Get the streaming member's shape if any; or None otherwise.
        """
        if not isinstance(shape, StructureShape):
            return None
        payload = shape.serialization.get("payload")
        if payload is not None:
            payload_shape = shape.members.get(payload)
            if isinstance(payload_shape, Shape) and payload_shape.type_name == "blob":
                return payload_shape
        return None

    def parse_shape(
        self,
        shape: Shape,
        output: bool = False,
        output_child: bool = False,
        is_streaming: bool = False,
    ) -> FakeAnnotation:
        """
        Parse any botocore shape to TypeAnnotation.

        Arguments:
            shape -- Botocore shape.
            output -- Whether shape should use strict output types.
            output_child -- Whether shape parent is marked as output.
            is_streaming -- Whether shape should be streaming.

        Returns:
            TypeAnnotation or similar class.
        """
        if "eventstream" in shape.serialization and shape.serialization["eventstream"]:
            shape.serialization["eventstream"] = False
            return TypeSubscript(
                ExternalImport.from_class(EventStream),
                [
                    self.parse_shape(
                        shape,
                        output=output,
                        output_child=output_child,
                        is_streaming=is_streaming,
                    )
                ],
                stringify=True,
            )
        is_output_or_child = output or output_child
        if not is_streaming:
            is_streaming = "streaming" in shape.serialization and shape.serialization["streaming"]
            if is_output_or_child:
                is_streaming = self._get_streaming_body(shape) is not None

        type_name = self._get_shape_type_name(shape)
        if is_streaming and shape.type_name == "blob":
            type_name = "blob_streaming"

        shape_type_stub = get_shape_type_stub(
            (
                OUTPUT_SHAPE_TYPE_MAP if is_output_or_child else {},
                SHAPE_TYPE_MAP,
            ),
            self.service_name,
            self._resource_name,
            type_name,
        )
        if shape_type_stub:
            return shape_type_stub

        if isinstance(shape, StringShape):
            return self._parse_shape_string(shape, output_child=is_output_or_child)

        if isinstance(shape, MapShape):
            return self._parse_shape_map(
                shape,
                output_child=is_output_or_child,
                is_streaming=is_streaming,
            )

        if isinstance(shape, StructureShape):
            return self._parse_shape_structure(
                shape,
                output=output,
                output_child=is_output_or_child,
                is_streaming=is_streaming,
            )

        if isinstance(shape, ListShape):
            return self._parse_shape_list(shape, output_child=is_output_or_child)

        if shape.type_name in self._get_resource_names():
            return AliasInternalImport(shape.type_name)

        self.logger.warning(f"Unknown shape: {shape} {type_name}")
        return Type.Any

    def get_paginate_method(self, paginator_name: str) -> Method:
        """
        Get Paginator `paginate` method.

        Arguments:
            paginator_name -- Paginator name.

        Returns:
            Method.
        """
        self._resource_name = "Paginator"
        operation_name = paginator_name
        paginator_shape = self._get_paginator(paginator_name)
        operation_shape = self._get_operation(operation_name)
        skip_argument_names: list[str] = []
        input_token: list[str] | str = paginator_shape["input_token"]
        if isinstance(input_token, list):
            skip_argument_names.extend(input_token)
        else:
            skip_argument_names.append(input_token)
        if "limit_key" in paginator_shape:
            skip_argument_names.append(paginator_shape["limit_key"])

        arguments: list[Argument] = [Argument("self", None)]

        if operation_shape.input_shape is not None:
            shape_arguments = self._parse_arguments(
                "Paginator",
                "paginate",
                operation_name,
                operation_shape.input_shape,
                exclude_names=skip_argument_names,
            )
            shape_arguments.append(
                Argument("PaginationConfig", PaginatorConfigTypeDef, Type.Ellipsis)
            )
            arguments.extend(self._get_kw_flags("paginate", shape_arguments))
            arguments.extend(shape_arguments)

        return_type: FakeAnnotation = Type.none
        if operation_shape.output_shape is not None:
            page_iterator_import = InternalImport("_PageIterator", stringify=False)
            return_item = self._parse_return_type(
                "Paginator", "paginate", operation_shape.output_shape
            )
            return_type = TypeSubscript(page_iterator_import, [return_item])

        method = Method("paginate", arguments, return_type)
        if operation_shape.input_shape is not None:
            method.create_request_type_annotation(
                self._get_typed_dict_name(
                    operation_shape.input_shape, postfix=f"{paginator_name}Paginate"
                )
            )
        return method

    def get_wait_method(self, waiter_name: str) -> Method:
        """
        Get Waiter `wait` method.

        Arguments:
            waiter_name -- Waiter name.

        Returns:
            Method.
        """
        self._resource_name = "Waiter"
        if not self._waiters_shape:
            raise ShapeParserError("Waiter not found")
        operation_name = self._waiters_shape["waiters"][waiter_name]["operation"]
        operation_shape = self._get_operation(operation_name)

        arguments: list[Argument] = [Argument("self", None)]

        if operation_shape.input_shape is not None:
            shape_arguments = self._parse_arguments(
                "Waiter", "wait", operation_name, operation_shape.input_shape
            )
            shape_arguments.append(Argument("WaiterConfig", WaiterConfigTypeDef, Type.Ellipsis))
            arguments.extend(self._get_kw_flags("wait", shape_arguments))
            arguments.extend(shape_arguments)

        method = Method(name="wait", arguments=arguments, return_type=Type.none)
        if operation_shape.input_shape is not None:
            method.create_request_type_annotation(
                self._get_typed_dict_name(operation_shape.input_shape, postfix=f"{waiter_name}Wait")
            )
        return method

    def get_service_resource_method_map(self) -> dict[str, Method]:
        """
        Get methods for ServiceResource.

        Returns:
            A map of method name to Method.
        """
        result: dict[str, Method] = {
            "get_available_subresources": Method(
                "get_available_subresources",
                [Argument("self", None)],
                TypeSubscript(Type.Sequence, [Type.str]),
            ),
        }
        self._resource_name = "ServiceResource"
        service_resource_shape = self._get_service_resource()
        for action_name, action_shape in service_resource_shape.get("actions", {}).items():
            method = self._get_resource_method(action_name, action_shape)
            result[method.name] = method

        for sub_resource_name in self._get_resource_names():
            resource_shape = self._get_resource_shape(sub_resource_name)
            arguments = [Argument("self", None)]
            identifiers: list[dict[str, str]] = resource_shape.get("identifiers", [])
            for identifier in identifiers:
                arguments.append(Argument(xform_name(identifier["name"]), Type.str))
            method = Method(
                sub_resource_name,
                arguments=arguments,
                return_type=AliasInternalImport(sub_resource_name),
            )
            result[method.name] = method

        return result

    def get_resource_method_map(self, resource_name: str) -> dict[str, Method]:
        """
        Get methods for Resource.

        Arguments:
            resource_name -- Resource name.

        Returns:
            A map of method name to Method.
        """
        self._resource_name = resource_name
        resource_shape = self._get_resource_shape(resource_name)
        result: dict[str, Method] = {
            "get_available_subresources": Method(
                "get_available_subresources",
                [Argument("self", None)],
                TypeSubscript(Type.Sequence, [Type.str]),
            ),
            "load": Method("load", [Argument("self", None)], Type.none),
            "reload": Method("reload", [Argument("self", None)], Type.none),
        }

        for action_name, action_shape in resource_shape.get("actions", {}).items():
            method = self._get_resource_method(action_name, action_shape)
            result[method.name] = method

        for waiter_name in resource_shape.get("waiters", {}):
            method = Method(
                f"wait_until_{xform_name(waiter_name)}",
                [Argument("self", None)],
                Type.none,
            )
            result[method.name] = method

        for sub_resource_name, sub_resource in resource_shape.get("has", {}).items():
            data: dict[str, Any] = sub_resource.get("resource", {})
            arguments = [Argument("self", None)]
            identifiers: list[dict[str, str]] = data.get("identifiers", [])
            for identifier in identifiers:
                if identifier.get("source") != "input":
                    continue
                arguments.append(Argument(xform_name(identifier["target"]), Type.str))

            method = Method(
                sub_resource_name,
                arguments=arguments,
                return_type=AliasInternalImport(data["type"]),
            )
            result[method.name] = method

        return result

    @staticmethod
    def _get_arg_from_target(target: str) -> str:
        if "[" in target:
            target = target.split("[")[0]
        if "." in target:
            target = target.split(".")[0]
        return target

    def _get_skip_argument_names(self, action_shape: dict[str, Any]) -> set[str]:
        result: set[str] = set()
        params = action_shape["request"].get("params", {})
        for param in params:
            target = param["target"]
            source = param["source"]
            if source == "identifier":
                result.add(self._get_arg_from_target(target))
                continue
            if source == "string" and "." in target:
                result.add(self._get_arg_from_target(target))
                continue

        return result

    def _enrich_arguments_defaults(
        self, arguments: list[Argument], action_shape: dict[str, Any]
    ) -> None:
        params = action_shape["request"].get("params", {})
        arguments_map = {a.name: a for a in arguments}
        for param in params:
            target = param["target"]
            source = param["source"]
            if source == "string" and target in arguments_map:
                arguments_map[target].default = TypeConstant(param["value"])

    def _get_resource_method(self, action_name: str, action_shape: dict[str, Any]) -> Method:
        return_type: FakeAnnotation = Type.none
        method_name = xform_name(action_name)
        arguments: list[Argument] = [Argument("self", None)]
        if "resource" in action_shape:
            return_type = self._parse_return_type(
                self.resource_name, method_name, Shape("resource", action_shape["resource"])
            )
            path = action_shape["resource"].get("path", "")
            if path.endswith("[]"):
                return_type = TypeSubscript(Type.List, [return_type])

        operation_shape = None
        if "request" in action_shape:
            operation_name = action_shape["request"]["operation"]
            operation_shape = self._get_operation(operation_name)
            skip_argument_names = self._get_skip_argument_names(action_shape)
            if operation_shape.input_shape is not None:
                shape_arguments = self._parse_arguments(
                    self.resource_name,
                    method_name,
                    operation_name,
                    operation_shape.input_shape,
                    exclude_names=skip_argument_names,
                )
                arguments.extend(self._get_kw_flags(method_name, shape_arguments))
                arguments.extend(shape_arguments)

            self._enrich_arguments_defaults(arguments, action_shape)
            arguments.sort(key=lambda x: not x.required)

            if operation_shape.output_shape is not None and return_type is Type.none:
                operation_return_type = self.parse_shape(operation_shape.output_shape, output=True)
                return_type = operation_return_type

        method = Method(name=method_name, arguments=arguments, return_type=return_type)
        if operation_shape and operation_shape.input_shape is not None:
            method.create_request_type_annotation(
                self._get_typed_dict_name(
                    operation_shape.input_shape, postfix=f"{self.resource_name}{action_name}"
                )
            )
        return method

    def get_collection_filter_method(
        self, name: str, collection: Collection, self_type: FakeAnnotation
    ) -> Method:
        """
        Get `filter` classmethod for Resource collection.

        Arguments:
            name -- Collection record name.
            collection -- Boto3 Collection.
            class_type -- Collection class type annotation.

        Returns:
            Filter Method record.
        """
        result = Method(
            name="filter",
            arguments=[Argument("self", None)],
            return_type=self_type,
        )
        if not collection.request:
            return result

        operation_name = collection.request.operation
        operation_model = self._get_operation(operation_name)

        if operation_model.input_shape is not None:
            shape_arguments = self._parse_arguments(
                name,
                result.name,
                operation_name,
                operation_model.input_shape,
                optional_only=True,
            )
            result.arguments.extend(self._get_kw_flags(result.name, shape_arguments))
            result.arguments.extend(shape_arguments)

        return result

    def get_collection_batch_methods(self, name: str, collection: Collection) -> list[Method]:
        """
        Get batch operations for Resource collection.

        Arguments:
            name -- Collection record name.
            collection -- Boto3 Collection.
            class_type -- Collection self type annotation.

        Returns:
            List of Method records.
        """
        result: list[Method] = []
        for batch_action in collection.batch_actions:
            method = Method(
                name=batch_action.name,
                arguments=[Argument("self", None)],
                return_type=Type.none,
            )
            result.append(method)
            if batch_action.request:
                operation_name = batch_action.request.operation
                operation_model = self._get_operation(operation_name)
                if operation_model.input_shape is not None:
                    shape_arguments = self._parse_arguments(
                        name,
                        batch_action.name,
                        operation_name,
                        operation_model.input_shape,
                        optional_only=True,
                    )
                    method.arguments.extend(self._get_kw_flags(batch_action.name, shape_arguments))
                    method.arguments.extend(shape_arguments)
                if operation_model.output_shape is not None:
                    item_return_type = self.parse_shape(operation_model.output_shape, output=True)
                    return_type = TypeSubscript(Type.List, [item_return_type])
                    method.return_type = return_type

        return result

    @staticmethod
    def _get_typed_dict_name_prefix(name: str) -> str:
        if name.endswith("OutputTypeDef"):
            return name[: -len("OutputTypeDef")]
        if name.endswith("TypeDef"):
            return name[: -len("TypeDef")]

        raise ShapeParserError(f"Unknown typed dict name format: {name}")

    def _get_typed_dict(
        self, name: str, maps: Sequence[dict[str, TypeTypedDict]]
    ) -> TypeTypedDict | None:
        for typed_dict_map in maps:
            if name in typed_dict_map:
                return typed_dict_map[name]
        return None

    def _get_non_clashing_typed_dict_name(self, typed_dict: TypeTypedDict, postfix: str) -> str:
        new_typed_dict_name = get_type_def_name(
            self._get_typed_dict_name_prefix(typed_dict.name), postfix
        )
        clashing_typed_dict = self._get_typed_dict(
            new_typed_dict_name,
            (
                self._typed_dict_map,
                self._output_typed_dict_map,
                self._response_typed_dict_map,
            ),
        )
        if not clashing_typed_dict or clashing_typed_dict.is_same(typed_dict):
            return new_typed_dict_name

        self.logger.debug(f"Clashing typed dict name found: {new_typed_dict_name}")
        return self._get_non_clashing_typed_dict_name(typed_dict, "Extra" + postfix)

    def fix_typed_dict_names(self) -> None:
        """
        Fix typed dict names to avoid duplicates.
        """
        output_typed_dicts = TypeDefSorter(self._output_typed_dict_map.values()).sort()
        output_typed_dict_names = [
            i.name for i in output_typed_dicts if i.name in self._output_typed_dict_map
        ]
        for name in output_typed_dict_names:
            typed_dict = self._get_typed_dict(
                name,
                (self._typed_dict_map,),
            )
            if typed_dict is None:
                continue

            typed_dict = self._typed_dict_map[name]
            output_typed_dict = self._output_typed_dict_map[name]
            if typed_dict.is_same(output_typed_dict):
                continue

            old_typed_dict_name = typed_dict.name
            new_typed_dict_name = self._get_non_clashing_typed_dict_name(typed_dict, "Output")
            self._fixed_typed_dict_map[typed_dict] = output_typed_dict
            self.logger.debug(
                f"Fixing TypedDict name clash {old_typed_dict_name} -> {new_typed_dict_name}"
            )

            output_typed_dict.name = new_typed_dict_name
            del self._output_typed_dict_map[old_typed_dict_name]
            self._output_typed_dict_map[output_typed_dict.name] = output_typed_dict
            # print(
            #     old_typed_dict_name,
            #     output_typed_dict.name,
            #     old_typed_dict_name in self._response_typed_dict_map,
            #     output_typed_dict.name in self._response_typed_dict_map,
            # )

            if old_typed_dict_name in self._response_typed_dict_map:
                del self._response_typed_dict_map[old_typed_dict_name]
                self._response_typed_dict_map[output_typed_dict.name] = output_typed_dict

        response_typed_dicts = TypeDefSorter(self._response_typed_dict_map.values()).sort()
        response_typed_dict_names = [
            i.name for i in response_typed_dicts if i.name in self._response_typed_dict_map
        ]
        # print(list(self._response_typed_dict_map.keys()))
        # print(response_typed_dict_names)
        for name in response_typed_dict_names:
            typed_dict = self._get_typed_dict(
                name,
                (
                    self._typed_dict_map,
                    self._output_typed_dict_map,
                ),
            )
            if typed_dict is None:
                continue

            response_typed_dict = self._response_typed_dict_map[name]
            if typed_dict.is_same(response_typed_dict):
                continue

            old_typed_dict_name = typed_dict.name
            new_typed_dict_name = self._get_non_clashing_typed_dict_name(typed_dict, "Response")
            self.logger.debug(
                f"Fixing TypedDict name clash {old_typed_dict_name} -> {new_typed_dict_name}"
            )

            response_typed_dict.name = new_typed_dict_name
            del self._response_typed_dict_map[old_typed_dict_name]
            self._response_typed_dict_map[response_typed_dict.name] = response_typed_dict

    def fix_method_arguments_for_mypy(self, methods: Sequence[Method]) -> None:
        """
        Accept both input and output shapes in method arguments.

        mypy does not compare TypedDicts, so we need to accept both input and output shapes.
        https://github.com/youtype/mypy_boto3_builder/issues/209
        """
        for input_typed_dict, output_typed_dict in self._fixed_typed_dict_map.items():
            for method in methods:
                for argument in method.arguments:
                    if not argument.type_annotation:
                        continue
                    if (
                        argument.type_annotation.is_typed_dict()
                        and argument.type_annotation == input_typed_dict
                    ):
                        self.logger.debug(
                            f"Adding output shape to {method.name} {argument.name} type:"
                            f" {input_typed_dict.name} | {output_typed_dict.name}"
                        )
                        union_name = get_type_def_name(
                            self._get_typed_dict_name_prefix(input_typed_dict.name), "Union"
                        )
                        argument.type_annotation = TypeUnion(
                            name=union_name,
                            children=[input_typed_dict, output_typed_dict],
                        )
                        continue
                    if isinstance(argument.type_annotation, TypeSubscript):
                        parent = argument.type_annotation.find_type_annotation_parent(
                            input_typed_dict
                        )
                        if parent:
                            self.logger.debug(
                                f"Adding output shape to {method.name} {argument.name} type:"
                                f" {input_typed_dict.name} | {output_typed_dict.name}"
                            )
                            union_name = get_type_def_name(
                                self._get_typed_dict_name_prefix(input_typed_dict.name), "Union"
                            )
                            parent.replace_child(
                                input_typed_dict,
                                TypeUnion(
                                    name=union_name,
                                    children=[input_typed_dict, output_typed_dict],
                                ),
                            )
                            continue
