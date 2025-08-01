from django.utils.translation import get_language


class TranslatableFieldMixin:
    """Mixin für automatische Sprachauswahl basierend auf Accept-Language"""

    def get_localized_name(self, obj, field_base='name'):
        """
        Retrieve a localized value for a specified field from an object, using language preference fallbacks.
        
        Attempts to return the value of the field with the current language code suffix (e.g., `name_en`). If unavailable or empty, falls back to the German (`_de`) version, then the English (`_en`) version, and finally the base field without a language suffix. Returns an empty string if no value is found.
        
        Parameters:
            obj: The object containing the translatable fields.
            field_base (str): The base name of the field to localize (default is 'name').
        
        Returns:
            str: The localized field value, or an empty string if none is available.
        """
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