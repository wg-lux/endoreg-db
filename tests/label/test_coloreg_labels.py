from django.test import TestCase

from endoreg_db.models import Label, LabelSet

from ..helpers.data_loader import load_ai_model_label_data


class ColoregLabelsTest(TestCase):
    def test_annotation_labels_are_loaded_from_yaml(self) -> None:
        load_ai_model_label_data()

        expected_names = {
            "bubbles",
            "cold_snare",
            "hot_snare",
            "tissue_on_snare",
            "smoke",
        }
        loaded_names = set(
            Label.objects.filter(name__in=expected_names).values_list("name", flat=True)
        )
        self.assertEqual(loaded_names, expected_names)

        coloreg_label_set = LabelSet.objects.get(
            name="multilabel_classification_colonoscopy_default",
            version=1,
        )
        label_set_names = set(coloreg_label_set.labels.values_list("name", flat=True))
        self.assertTrue(expected_names.issubset(label_set_names))
        self.assertEqual(
            set(
                Label.objects.filter(name__in={"cold_snare", "hot_snare"}).values_list(
                    "label_type__name", flat=True
                )
            ),
            {"classification"},
        )
