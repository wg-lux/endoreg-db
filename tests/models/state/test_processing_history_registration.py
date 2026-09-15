from django.apps import apps

from endoreg_db.models import ProcessingHistory
from endoreg_db.models.state import ProcessingHistory as StateProcessingHistory


def test_processing_history_is_registered_for_no_migration_schema_builds() -> None:
    assert StateProcessingHistory is ProcessingHistory
    assert apps.get_model("endoreg_db", "ProcessingHistory") is ProcessingHistory
    assert ProcessingHistory._meta.db_table == "endoreg_db_processinghistory"
