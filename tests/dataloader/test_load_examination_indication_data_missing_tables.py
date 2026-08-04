from endoreg_db.management.commands.load_examination_indication_data import Command


def test_load_from_dtypes_skips_when_required_tables_are_missing(monkeypatch):
    command = Command()
    writes: list[str] = []

    monkeypatch.setattr(
        "endoreg_db.management.commands.load_examination_indication_data.connection.introspection.table_names",
        lambda: [],
    )
    monkeypatch.setattr(
        "endoreg_db.management.commands.load_examination_indication_data._load_dtypes_knowledge_base",
        lambda module_name: (_ for _ in ()).throw(AssertionError("should not load")),
    )
    monkeypatch.setattr(command.stdout, "write", writes.append)
    monkeypatch.setattr(command.style, "WARNING", lambda message: message)

    command._load_from_dtypes(
        verbose=True,
        module_name="lx_examinations",
        strict=False,
    )

    assert writes == [
        "[dtypes] Skipping load because database tables are not available yet: "
        "endoreg_db_examination, endoreg_db_examinationindication, "
        "endoreg_db_examinationindicationclassification, "
        "endoreg_db_findingintervention, endoreg_db_informationsource"
    ]
