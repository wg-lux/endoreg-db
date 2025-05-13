from django.apps import AppConfig


class EndoregDbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'endoreg_db'
    
    def ready(self):
        """
        This method is called when the application is ready.
        It can be used to perform any startup tasks or register signals.
        """
        import endoreg_db.models.media.video
        import endoreg_db.models.media.frame
        import endoreg_db.models.media.pdf
        pass
