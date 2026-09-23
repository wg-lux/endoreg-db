import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AIDATASET_MODEL_PATH = (
    PROJECT_ROOT / "endoreg_db" / "models" / "aidataset" / "aidataset.py"
)
SENSITIVE_META_UPDATE_SERIALIZER_PATH = (
    PROJECT_ROOT / "endoreg_db" / "serializers" / "meta" / "sensitive_meta_update.py"
)
SENSITIVE_METADATA_VIEW_PATH = (
    PROJECT_ROOT / "endoreg_db" / "views" / "media" / "sensitive_metadata.py"
)

AIDATASET_LEGACY_SERVICE_METHODS = frozenset(
    {
        "_coerce_active_learning_candidates",
        "_select_active_learning_candidates_locally",
        "select_active_learning_frame_indices_from_candidates",
        "select_active_learning_frame_indices",
        "build_frame_bucket_distribution",
        "build_export_payload",
        "export_to_standardized_structure",
    }
)
SENSITIVE_METADATA_UPDATE_VIEW_FUNCTIONS = frozenset(
    {
        "_update_sensitive_metadata",
        "video_sensitive_metadata",
        "pdf_sensitive_metadata",
    }
)
PERSISTENCE_MUTATION_METHODS = frozenset(
    {
        "create",
        "delete",
        "get_or_create",
        "save",
        "update",
        "update_from_dict",
        "update_or_create",
    }
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _qualified_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _qualified_name(node.func)
    return ""


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = path.parent.relative_to(PROJECT_ROOT).parts
    parent_hops = node.level - 1
    if parent_hops > len(package_parts):
        return ""
    base_parts = package_parts[: len(package_parts) - parent_hops]
    module_parts = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*base_parts, *module_parts))


def _service_imports(path: Path, tree: ast.AST) -> set[tuple[int, str]]:
    imports: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(path, node)
            if module == "endoreg_db.services" or module.startswith(
                "endoreg_db.services."
            ):
                imports.add((node.lineno, module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "endoreg_db.services" or alias.name.startswith(
                    "endoreg_db.services."
                ):
                    imports.add((node.lineno, alias.name))
    return imports


def _class_definition(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Missing class {name}")


def _function_definition(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing function {name}")


def _defined_methods(class_node: ast.ClassDef) -> set[str]:
    return {
        node.name
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _transaction_atomic_references(scope: ast.AST) -> set[tuple[int, str]]:
    references: set[tuple[int, str]] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Attribute) and _qualified_name(node) in {
            "django.db.transaction.atomic",
            "transaction.atomic",
        }:
            references.add((node.lineno, _qualified_name(node)))
    return references


def _persistence_mutation_calls(scope: ast.AST) -> set[tuple[int, str]]:
    calls: set[tuple[int, str]] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        qualified_name = _qualified_name(node.func)
        method_name = qualified_name.rsplit(".", maxsplit=1)[-1]
        if method_name in PERSISTENCE_MUTATION_METHODS:
            calls.add((node.lineno, qualified_name))
    return calls


def test_aidataset_model_has_no_service_imports() -> None:
    tree = _parse(AIDATASET_MODEL_PATH)

    assert not _service_imports(AIDATASET_MODEL_PATH, tree)


def test_aidataset_model_has_no_legacy_service_methods() -> None:
    tree = _parse(AIDATASET_MODEL_PATH)
    model = _class_definition(tree, "AIDataSet")

    assert not AIDATASET_LEGACY_SERVICE_METHODS.intersection(_defined_methods(model))


def test_sensitive_meta_update_serializer_is_validation_only() -> None:
    tree = _parse(SENSITIVE_META_UPDATE_SERIALIZER_PATH)
    serializer = _class_definition(tree, "SensitiveMetaUpdateSerializer")

    assert not {"create", "update"}.intersection(_defined_methods(serializer))
    assert not _transaction_atomic_references(serializer)


def test_sensitive_metadata_update_views_do_not_mutate_persistence() -> None:
    tree = _parse(SENSITIVE_METADATA_VIEW_PATH)
    mutation_calls = {
        function_name: _persistence_mutation_calls(
            _function_definition(tree, function_name)
        )
        for function_name in SENSITIVE_METADATA_UPDATE_VIEW_FUNCTIONS
    }

    assert not {name: calls for name, calls in mutation_calls.items() if calls}
