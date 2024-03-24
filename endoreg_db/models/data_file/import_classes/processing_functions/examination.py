from ..raw_pdf import RawPdfFile
from logging import getLogger
#
# # setup logging to pdf_import.log
logger = getLogger('examination_pdf_import')

def get_examination_reports_scheduled_for_processing():
    reports = RawPdfFile.objects.filter(
        state_report_processing_required=True
    )
    return reports

def process_examination_report(report:RawPdfFile):
    if report.update(save=True, verbose=True):
        logger.info(f"Report {report} processed successfully")
        return True
    else:
        logger.error(f"Report {report} processing failed")
        return False


def process_examination_reports():
    reports = get_examination_reports_scheduled_for_processing()
    for report in reports:
        process_examination_report(report)
    


