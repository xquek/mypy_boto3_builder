"""
Wrapper for name Union type annotations, like `MyUnion = Union[str, int]`.
"""
from collections.abc import Iterable, Iterator
from typing import TypeVar

from mypy_boto3_builder.enums.service_module_name import ServiceModuleName
from mypy_boto3_builder.import_helpers.import_record import ImportRecord
from mypy_boto3_builder.import_helpers.internal_import_record import InternalImportRecord
from mypy_boto3_builder.type_annotations.fake_annotation import FakeAnnotation
from mypy_boto3_builder.type_annotations.type import Type
from mypy_boto3_builder.type_annotations.type_def_sortable import TypeDefSortable
from mypy_boto3_builder.type_annotations.type_literal import TypeLiteral
from mypy_boto3_builder.type_annotations.type_subscript import TypeSubscript

_R = TypeVar("_R", bound="TypeUnion")


class TypeUnion(TypeSubscript, TypeDefSortable):
    """
    Wrapper for name Union type annotations, like `MyUnion = Union[str, int]`.
    """

    def __init__(
        self,
        children: Iterable[FakeAnnotation],
        name: str = "",
        stringify: bool = False,
    ) -> None:
        self.name = name
        self.parent = Type.Union
        self.children: list[FakeAnnotation] = list(children)
        if len(self.children) < 2:
            raise ValueError("Union must have at least 2 children")
        self._stringify = stringify

    def is_stringified(self) -> bool:
        """
        Whether Union usage should be rendered as a string.
        """
        return self._stringify

    def stringify(self) -> None:
        """
        Render Union usage as a string.
        """
        self._stringify = True

    def render(self, parent_name: str = "") -> str:
        """
        Render type annotation to a valid Python code for local usage.

        Returns:
            A string with a valid type annotation.
        """
        if not self.is_named():
            return super().render(parent_name)

        result = self.name

        if self._stringify:
            result = f'"{result}"'

        return result

    def is_named(self) -> bool:
        """
        Check if type annotation is a named type annotation.
        """
        return self.name != ""

    def copy(self: _R) -> _R:
        """
        Create a copy of type annotation wrapper.
        """
        return self.__class__(
            children=list(self.children),
            name=self.name,
            stringify=self._stringify,
        )

    def debug_render(self) -> str:
        """
        Render type annotation for debug purposes.
        """
        return f"{self}: {', '.join([c.render() for c in self.children])}"

    def get_children_types(self) -> set[FakeAnnotation]:
        """
        Extract required type annotations from attributes.
        """
        result: set[FakeAnnotation] = set()
        for child in self.children:
            result.update(child.iterate_types())
        return result

    def get_sortable_children(self) -> list[TypeDefSortable]:
        """
        Extract required TypeDefSortable list from attributes.
        """
        result: list[TypeDefSortable] = []
        children_types = self.get_children_types()
        for type_annotation in children_types:
            if not isinstance(type_annotation, TypeDefSortable):
                continue
            result.append(type_annotation)

        return result

    def get_children_literals(self, processed: Iterable[str] = ()) -> set[TypeLiteral]:
        """
        Extract required TypeLiteral list from attributes.
        """
        result: set[TypeLiteral] = set()
        if self.name in processed:
            return result
        children_types = self.get_children_types()
        for type_annotation in children_types:
            if isinstance(type_annotation, TypeLiteral):
                result.add(type_annotation)
            if isinstance(type_annotation, TypeDefSortable):
                result.update(type_annotation.get_children_literals((self.name, *processed)))
        return result

    def get_definition_import_records(self) -> set[ImportRecord]:
        """
        Get import record required for using Union.
        """
        result: set[ImportRecord] = set()
        result.update(self.parent.get_import_records())
        for child in self.children:
            result.update(child.get_import_records())
        return result

    def _get_import_records(self) -> set[ImportRecord]:
        """
        Get import record required for using type annotation.
        """
        return {InternalImportRecord(ServiceModuleName.type_defs, name=self.name)}

    def get_import_records(self) -> set[ImportRecord]:
        """
        Get all import records required for using type annotation.
        """
        return self._get_import_records()

    def iterate_types(self) -> Iterator[FakeAnnotation]:
        """
        Extract type annotations from children.
        """
        if self.is_named():
            yield self
            return

        yield from super().iterate_types()

    def is_type_def(self) -> bool:
        """
        Whether type annotation is a TypeDef.
        """
        return self.is_named()

    @property
    def type_hint_annotations(self) -> list[FakeAnnotation]:
        """
        Type annotations list from arguments and return type with internal types.
        """
        result: list[FakeAnnotation] = []
        for child in self.children:
            if child.get_local_types():
                result.append(child)
        return result

    def is_union(self) -> bool:
        """
        Whether type annotation is a TypeUnion.
        """
        return self.parent == Type.Union

    def iterate_children(self) -> Iterator[FakeAnnotation]:
        """
        Iterate over children.
        """
        yield from self.children
