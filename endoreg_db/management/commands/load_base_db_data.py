from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = "Run all data loading commands in the correct order"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output for all commands",
        )
        parser.add_argument(
            "--include-legacy-requirements",
            action="store_true",
            help="Include legacy requirement graph seed data (compatibility only).",
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
        call_command("load_finding_data", stdout=out, verbose=verbose)
        # 1) Seed legacy YAML indication rows first so examination YAML can resolve FKs.
        call_command(
            "load_examination_indication_data",
            stdout=out,
            verbose=verbose,
            source="yaml",
        )
        call_command("load_examination_data", stdout=out, verbose=verbose)
        # 2) Overlay with lx_dtypes terminology and sync examination->indication links.
        call_command(
            "load_examination_indication_data",
            stdout=out,
            verbose=verbose,
            source="hybrid",
        )
        call_command("load_lab_value_data", stdout=out, verbose=verbose)
        call_command("load_medication_data", stdout=out, verbose=verbose)

        if options.get("include_legacy_requirements", False):
            call_command("load_requirement_data", stdout=out, verbose=verbose)
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Skipping load_requirement_data (legacy compatibility). "
                    "Use --include-legacy-requirements to enable."
                )
            )

        # Load AI Model Data
        call_command("load_ai_model_label_data", stdout=out, verbose=verbose)
        call_command("load_ai_model_data", stdout=out, verbose=verbose)

        self.stdout.write(
            self.style.SUCCESS(  # pylint: disable=no-member
                "All data loading commands executed successfully."
            )
        )
