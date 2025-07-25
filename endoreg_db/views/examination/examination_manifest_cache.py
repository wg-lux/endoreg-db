from django.core.cache import cache


class ExaminationManifestCache:
    """Cache-Manager für Examination Manifests"""

    @staticmethod
    def get_cache_key(examination_id, language='en'):
        return f"examination_manifest:{examination_id}:{language}"

    @staticmethod
    def get_manifest(examination_id, language='en'):
        cache_key = ExaminationManifestCache.get_cache_key(examination_id, language)
        return cache.get(cache_key)

    @staticmethod
    def set_manifest(examination_id, data, language='en', timeout=60*60):  # 1 Stunde
        cache_key = ExaminationManifestCache.get_cache_key(examination_id, language)
        cache.set(cache_key, data, timeout)

    @staticmethod
    def invalidate_manifest(examination_id):
        """Invalidiere Cache für alle Sprachen"""
        for lang in ['en', 'de']:
            cache_key = ExaminationManifestCache.get_cache_key(examination_id, lang)
            cache.delete(cache_key)