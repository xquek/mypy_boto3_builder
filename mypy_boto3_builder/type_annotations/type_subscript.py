"""
Wrapper for subscript type annotations, like `List[str]`.
"""
from collections.abc import Iterable, Iterator
from typing import TypeVar

from mypy_boto3_builder.import_helpers.import_record import ImportRecord
from mypy_boto3_builder.type_annotations.fake_annotation import FakeAnnotation

_R = TypeVar("_R", bound="TypeSubscript")


class TypeSubscript(FakeAnnotation):
    """
    Wrapper for subscript type annotations, like `List[str]`.

    Arguments:
        parent -- Parent type annotation.
        children -- Children type annotations.
        stringify -- Convert type annotation to string.
    """

    def __init__(
        self,
        parent: FakeAnnotation,
        children: Iterable[FakeAnnotation] = (),
        stringify: bool = False,
    ) -> None:
        self.parent: FakeAnnotation = parent
        self.children: list[FakeAnnotation] = list(children)
        self._stringify = stringify

    def __hash__(self) -> int:
        return hash(f"{self.parent}.{self.children}")

    def render(self, parent_name: str = "") -> str:
        """
        Render type annotation to a valid Python code for local usage.

        Returns:
            A string with a valid type annotation.
        """
        result = self.parent.render()
        if self.children:
            children = ", ".join([i.render(parent_name) for i in self.children])
            result = f"{result}[{children}]"

        if self._stringify:
            result = f'"{result}"'
        return result

    def get_import_records(self) -> set[ImportRecord]:
        """
        Get all import records required for using type annotation.
        """
        result: set[ImportRecord] = set()
        result.update(self.parent.get_import_records())
        for child in self.children:
            result.update(child.get_import_records())
        return result

    def iterate_types(self) -> Iterator[FakeAnnotation]:
        """
        Extract type annotations from children.
        """
        yield from self.parent.iterate_types()
        for child in self.children:
            yield from child.iterate_types()

    def add_child(self, child: FakeAnnotation) -> None:
        """
        Add new child to Substcript.
        """
        self.children.append(child)

    def is_dict(self) -> bool:
        """
        Whether subscript parent is Dict.
        """
        return self.parent.is_dict()

    def is_list(self) -> bool:
        """
        Whether subscript parent is List.
        """
        return self.parent.is_list()

    def copy(self: _R) -> _R:
        """
        Create a copy of type annotation wrapper.
        """
        return self.__class__(
            parent=self.parent,
            children=list(self.children),
            stringify=self._stringify,
        )

    def get_local_types(self) -> list[FakeAnnotation]:
        """
        Get internal types generated by builder.
        """
        result: list[FakeAnnotation] = []
        for child in self.children:
            result.extend(child.get_local_types())
        return result

    def find_type_annotation_parent(self: _R, type_annotation: FakeAnnotation) -> _R | None:
        """
        Check recursively if child is present in subscript.
        """
        if type_annotation in self.children:
            return self

        type_subscript_children = [i for i in self.children if isinstance(i, self.__class__)]
        for child in type_subscript_children:
            result = child.find_type_annotation_parent(type_annotation)
            if result:
                return result

        return None

    def replace_child(self: _R, child: FakeAnnotation, new_child: FakeAnnotation) -> _R:
        """
        Replace child type annotation with a new one.
        """
        if child not in self.children:
            raise ValueError(f"Child not found: {child}")

        index = self.children.index(child)
        self.children[index] = new_child
        return self
