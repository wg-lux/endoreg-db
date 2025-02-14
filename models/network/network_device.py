# Django db class to store network devices (e.g., servers, clients, switches, etc.)

from django.db import models

class NetworkDeviceManager(models.Manager):
    # Custom manager for NetworkDevice; defines name as natural key
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class NetworkDevice(models.Model):
    name = models.CharField(max_length=255)
    ip = models.GenericIPAddressField(blank=True, null=True)
    description = models.CharField(max_length=255)
    device_type = models.ForeignKey("NetworkDeviceType", on_delete=models.CASCADE)
    online = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects = NetworkDeviceManager()

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'network_devices'
        ordering = ['name']
        
    def natural_key(self):
        return (self.name,)
    
    def ping(self):
        target_ip = self.ip

        # Import the required module
        import subprocess

        # Define the command
        command = ['ping', '-c', '1', target_ip]

        # Run the command
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Wait for the process to terminate
        process.wait()

        # Check the return code
        return_code = process.returncode
        
        # Return True if the return code is 0, False otherwise
        self.online = return_code == 0
        self.save()
        return self.online
    

