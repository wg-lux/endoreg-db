from django.core.management import BaseCommand, call_command
from io import StringIO


class Command(BaseCommand):
    help = "Run all data loading commands in the correct order"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output for all commands",
        )

    def handle(self, *args, **options):
        # verbose = options['verbose']
        """
        Orchestrates the sequential execution of data loading commands to populate base database models.
        
        This management command displays an initial message and then runs a series of data loading routines 
        (via call_command) in a specified order. It ignores any verbose setting from the command-line options 
        and forces verbose output. A final success message is printed after all commands complete.
        """
        verbose = True

        self.stdout.write(self.style.SUCCESS("Populating base db models with data..."))

        out = self.stdout

        call_command("load_tag_data", stdout=out, verbose=verbose)
        call_command("load_information_source", stdout=out, verbose=verbose)

        call_command("load_risk_data", stdout=out, verbose=verbose)

        # Load Center Data
        call_command("load_center_data", stdout=out, verbose=verbose)
        call_command("load_endoscope_data", stdout=out, verbose=verbose)
        call_command("load_distribution_data", stdout=out, verbose=verbose)

        call_command("load_gender_data", stdout=out, verbose=verbose)
        call_command("load_report_reader_flag_data", stdout=out, verbose=verbose)
        call_command("load_pdf_type_data", stdout=out, verbose=verbose)
        call_command("load_unit_data", stdout=out, verbose=verbose)
        call_command("load_disease_data", stdout=out, verbose=verbose)
        call_command("load_event_data", stdout=out, verbose=verbose)
        call_command("load_organ_data", stdout=out, verbose=verbose)
        call_command("load_contraindication_data", stdout=out, verbose=verbose)
        call_command("load_examination_data", stdout=out, verbose=verbose)
        call_command("load_lab_value_data", stdout=out, verbose=verbose)
        call_command("load_finding_data", stdout=out, verbose=verbose)
        call_command("load_examination_indication_data", stdout=out, verbose=verbose)
        call_command("load_medication_data", stdout=out, verbose=verbose)

        call_command("load_requirement_data", stdout=out, verbose=verbose)

        # Load AI Model Data
        call_command("load_ai_model_label_data", stdout=out, verbose=verbose)
        call_command("load_ai_model_data", stdout=out, verbose=verbose)

        self.stdout.write(
            self.style.SUCCESS(  # pylint: disable=no-member
                "All data loading commands executed successfully."
            )
        )

        # # Run the load_network_data command
        # self.stdout.write(self.style.SUCCESS("Running load_network_data..."))
        # call_command('load_network_data', verbose=verbose)
        # Run the load_network_data command
        # self.stdout.write(self.style.SUCCESS("Running load_network_data..."))
        # call_command("load_network_data", verbose=verbose)

        # # Run the load_logging_data command
        # self.stdout.write(self.style.SUCCESS("Running load_logging_data..."))
        # call_command("load_logging_data", verbose=verbose)

        # # Run the load_profession_data command
        # self.stdout.write(self.style.SUCCESS("Running load_profession_data..."))
        # call_command("load_profession_data", verbose=verbose)

        # # run the load_gender_data command
        # self.stdout.write(self.style.SUCCESS("Running load_gender_data..."))
        # call_command("load_gender_data", verbose=verbose)

        # # Run the load_examination_data command
        # self.stdout.write(self.style.SUCCESS("Running load_examination_data..."))
        # call_command("load_examination_data", verbose=verbose)

        # # run the load_finding_data command
        # self.stdout.write(self.style.SUCCESS("Running load_finding_data..."))
        # call_command("load_finding_data", verbose=verbose)

        # # Run the load_disease data command
        # self.stdout.write(self.style.SUCCESS("Running load_disease_data..."))
        # call_command("load_disease_data", verbose=verbose)

        # # # Run the load_disease_classification data command
        # # self.stdout.write(self.style.SUCCESS("Running load_disease_classification_data..."))
        # # call_command('load_disease_classification_data', verbose=verbose)

        # # # Run the load_disease_classification_choices data command
        # # self.stdout.write(self.style.SUCCESS("Running load_disease_classification_choices_data..."))
        # # call_command('load_disease_classification_choices_data', verbose=verbose)

        # # rund the load_user_groups command
        # self.stdout.write(self.style.SUCCESS("Running load_user_groups..."))
        # call_command("load_user_groups", verbose=verbose)

        # # run the load_report_reader_flag command
        # self.stdout.write(self.style.SUCCESS("Running load_report_reader_flag..."))
        # call_command("load_report_reader_flag", verbose=verbose)

        # # Run the load_pdf_type_data command
        # self.stdout.write(self.style.SUCCESS("Running load_pdf_type_data..."))
        # call_command("load_pdf_type_data", verbose=verbose)

        # # Run the load_endoscopy_processor_data command
        # self.stdout.write(
        #     self.style.SUCCESS("Running load_endoscopy_processor_data...")
        # )
        # call_command("load_endoscopy_processor_data", verbose=verbose)

        # # Run the load_endoscope_type_data command
        # self.stdout.write(self.style.SUCCESS("Running load_endoscope_type_data..."))
        # call_command("load_endoscope_type_data", verbose=verbose)

        # # Run the load_unit_data command
        # self.stdout.write(self.style.SUCCESS("Running load_unit_data..."))
        # call_command("load_unit_data", verbose=verbose)

        # # Run the load_lab_value_data command
        # self.stdout.write(self.style.SUCCESS("Running load_lab_value_data..."))
        # call_command("load_lab_value_data", verbose=verbose)

        # # Run the load_information_source command
        # self.stdout.write(self.style.SUCCESS("Running load_information_source..."))
        # call_command("load_information_source", verbose=verbose)

        # # Run the load_center_data command
        # self.stdout.write(self.style.SUCCESS("Running load_center_data..."))
        # call_command("load_center_data", verbose=verbose)

        # # Run the load_label_data command
        # self.stdout.write(self.style.SUCCESS("Running load_label_data..."))
        # call_command("load_label_data", verbose=verbose)

        # # Run the load_ai_model_data command
        # self.stdout.write(self.style.SUCCESS("Running load_ai_model_data..."))
        # call_command("load_ai_model_data", verbose=verbose)

        # # Run the load_event_data command
        # self.stdout.write(self.style.SUCCESS("Running load_event_data..."))
        # call_command("load_event_data", verbose=verbose)

        # # run the load_medication_data command
        # self.stdout.write(self.style.SUCCESS("Running load_medication_data..."))
        # call_command("load_medication_data", verbose=verbose)

        # # # Run the load_medication_indication_type_data command
        # # self.stdout.write(self.style.SUCCESS("Running load_medication_indication_type_data..."))
        # # call_command('load_medication_indication_type_data', verbose=verbose)

        # # # Run the load_medication_intake_time_data command
        # # self.stdout.write(self.style.SUCCESS("Running load_medication_intake_time_data..."))
        # # call_command('load_medication_intake_time_data', verbose=verbose)

        # # # Run the load_medication_schedule_data command
        # # self.stdout.write(self.style.SUCCESS("Running load_medication_schedule_data..."))
        # # call_command('load_medication_schedule_data', verbose=verbose)

        # # # Run the load_medication_indication_data command
        # # self.stdout.write(self.style.SUCCESS("Running load_medication_indication_data..."))
        # # call_command('load_medication_indication_data', verbose=verbose)

        # # Run the load_green_endoscopy_wuerzburg_data command
        # self.stdout.write(
        #     self.style.SUCCESS("Running load_green_endoscopy_wuerzburg_data...")
        # )
        # call_command("load_green_endoscopy_wuerzburg_data", verbose=verbose)

        # # Load G-Play Data
        # # run the load_distribution_data command
        # self.stdout.write(self.style.SUCCESS("Running load_distribution_data..."))
        # call_command("load_distribution_data", verbose=verbose)
        # # Run the load_g_play_data command
        # self.stdout.write(self.style.SUCCESS("Running load_g_play_data..."))
        # call_command("load_g_play_data", verbose=verbose)

        # # Necessary? Migrate? FIXME
        # # # Run the load_active_model_data command
        # # self.stdout.write(self.style.SUCCESS("Running load_active_model_data..."))
        # # call_command('load_active_model_data', verbose=verbose)
