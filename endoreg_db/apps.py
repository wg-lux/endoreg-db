from django.apps import AppConfig


class EndoregDbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'endoreg_db'
    
    def ready(self):
        """
        Executes initialization code when the Django application is fully loaded.
        
        Imports media-related model modules to ensure their registration or signal setup at startup.
        """
        import endoreg_db.models.media.video
        import endoreg_db.models.media.frame
        import endoreg_db.models.media.pdf
        pass
