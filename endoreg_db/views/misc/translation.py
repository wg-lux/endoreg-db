"""
Automatisierte Internationalisierung für medizinische Terminologie
Ersetzt manuelle name_de/name_en Felder durch django-modeltranslation
"""
from modeltranslation.translator import TranslationOptions, translator
# Source: https://github.com/deschler/django-modeltranslation
#https://django-modeltranslation.readthedocs.io/en/latest/installation.html#advanced-settings

from endoreg_db.models import (
    Examination,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention
)

class ExaminationTranslationOptions(TranslationOptions):
    fields = ('name', 'description')
    required_languages = ('de', 'en')
    fallback_languages = {'default': ('en', 'de')}

translator.register(Examination, ExaminationTranslationOptions)


class FindingTranslationOptions(TranslationOptions):
    fields = ('name', 'description')
    required_languages = ('de', 'en')
    fallback_languages = {'default': ('en', 'de')}

translator.register(Finding, FindingTranslationOptions)


class FindingClassificationTranslationOptions(TranslationOptions):
    fields = ('name', 'description')
    required_languages = ('de', 'en')
    fallback_languages = {'default': ('en', 'de')}

translator.register(FindingClassification, FindingClassificationTranslationOptions)


class FindingClassificationChoiceTranslationOptions(TranslationOptions):
    fields = ('name', 'description')
    required_languages = ('de', 'en')
    fallback_languages = {'default': ('en', 'de')}

translator.register(FindingClassificationChoice, FindingClassificationChoiceTranslationOptions)


class InterventionTranslationOptions(TranslationOptions):
    fields = ('name', 'description')
    required_languages = ('de', 'en')
    fallback_languages = {'default': ('en', 'de')}

translator.register(FindingIntervention, InterventionTranslationOptions)


# Mixin für automatische Sprachauswahl in Serializers
class TranslatedFieldMixin:
    """
    Mixin für Serializer um automatisch die richtige Sprache zu wählen
    Ersetzt manuelle get_name_display() Methoden
    """
    
    def get_translated_field(self, obj, field_name):
        """
        Automatische Sprachauswahl basierend auf Accept-Language Header
        """
        request = self.context.get('request')
        if request:
            # Django's get_language() berücksichtigt Accept-Language Header
            from django.utils.translation import get_language
            current_language = get_language()
            
            # Versuche sprachspezifisches Feld
            translated_field = f"{field_name}_{current_language}"
            translated_value = getattr(obj, translated_field, None)
            
            if translated_value:
                return translated_value
        
        # Fallback auf Hauptfeld
        return getattr(obj, field_name, '')
    
    def to_representation(self, instance):
        """
        Automatisches Ersetzen von name/description mit übersetzten Versionen
        """
        data = super().to_representation(instance)
        
        # Ersetze name mit übersetzter Version
        if 'name' in data:
            data['name'] = self.get_translated_field(instance, 'name')
        
        # Ersetze description mit übersetzter Version
        if 'description' in data:
            data['description'] = self.get_translated_field(instance, 'description')
        
        return data


# Command für Migration bestehender Daten
class TranslationMigrationHelper:
    """
    Helper für Migration von name_de/name_en zu modeltranslation
    """
    
    @staticmethod
    def migrate_examination_data():
        """Migriere bestehende Examination Daten"""
        from endoreg_db.models import Examination
        
        for examination in Examination.objects.all():
            # Migriere name_de/name_en zu name_de/name_en (modeltranslation)
            if hasattr(examination, 'name_de') and examination.name_de:
                examination.name_de = examination.name_de
            if hasattr(examination, 'name_en') and examination.name_en:
                examination.name_en = examination.name_en
            
            # Fallback auf 'name' wenn Übersetzungen fehlen
            if not examination.name_de and examination.name:
                examination.name_de = examination.name
            if not examination.name_en and examination.name:
                examination.name_en = examination.name
                
            examination.save()
    
    @staticmethod
    def migrate_all_models():
        """Migriere alle Modelle mit Übersetzungen"""
        models_to_migrate = [
            'Examination', 'Finding', 'FindingClassification', 
            'FindingClassificationChoice', 'Intervention'
        ]
        
        for model_name in models_to_migrate:
            print(f"Migriere {model_name}...")
            # Implementierung für jedes Modell analog zu migrate_examination_data()


# Erweiterte YAML Fixtures für Übersetzungen
class TranslatedFixtureLoader:
    """
    Loader für YAML Fixtures mit Übersetzungsunterstützung
    Automatische Erkennung und Laden von name_de/name_en Feldern
    """
    
    @staticmethod
    def load_translated_fixture(model_class, fixture_data):
        """
        Lade Fixture mit automatischer Übersetzungszuordnung
        """
        for item in fixture_data:
            fields = item.get('fields', {})
            
            # Erstelle oder aktualisiere Objekt
            obj, created = model_class.objects.update_or_create(
                name=fields.get('name'),
                defaults=fields
            )
            
            # Setze Übersetzungen falls vorhanden
            if 'name_de' in fields:
                obj.name_de = fields['name_de']
            if 'name_en' in fields:
                obj.name_en = fields['name_en']
            
            obj.save()
            
            action = "erstellt" if created else "aktualisiert"
            print(f"{model_class.__name__} '{obj.name}' {action}")


# Settings für django-modeltranslation
MODELTRANSLATION_SETTINGS = {
    'AVAILABLE_LANGUAGES': ('de', 'en'),
    'DEFAULT_LANGUAGE': 'en',
    'FALLBACK_LANGUAGES': {
        'default': ('en', 'de'),
    },
    'AUTO_POPULATE': True,  # Automatisches Füllen fehlender Übersetzungen
    'ENABLE_REGISTRATIONS': True,
}