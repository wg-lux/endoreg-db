from django.apps import AppConfig


class EndoregDbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'endoreg_db'
    
    def ready(self):
        """
        Performs application startup tasks when the Django app is fully loaded.
        
        This method imports media-related model modules to ensure they are registered
        and ready for use when the application starts.
        """
        import endoreg_db.models.media.video
        import endoreg_db.models.media.frame
        import endoreg_db.models.media.pdf
        pass
