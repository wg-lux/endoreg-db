from django.utils.translation import get_language


class TranslatableFieldMixin:
    """Mixin für automatische Sprachauswahl basierend auf Accept-Language"""

    def get_localized_name(self, obj, field_base='name'):
        """Intelligente Sprachauswahl mit Fallback-Logik"""
        current_lang = get_language() or 'en'

        # Versuche sprachspezifisches Feld
        lang_field = f"{field_base}_{current_lang}"
        if hasattr(obj, lang_field):
            value = getattr(obj, lang_field)
            if value:
                return value

        # Fallback auf Deutsch
        de_field = f"{field_base}_de"
        if hasattr(obj, de_field):
            value = getattr(obj, de_field)
            if value:
                return value

        # Fallback auf Englisch
        en_field = f"{field_base}_en"
        if hasattr(obj, en_field):
            value = getattr(obj, en_field)
            if value:
                return value

        # Letzter Fallback auf Basis-Feld
        return getattr(obj, field_base, '')