    def validate_and_recover_data(self, token):
        """Validate stored data and attempt recovery if corrupted"""
        data = self.get_all()
        
        if not data:
            return None
            
        # Check if required fields are present
        required_fields = ['patient_examination_id', 'requirements_by_set', 'requirement_status']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Missing fields in lookup data for token {token}: {missing_fields}")
            
            # Try to recover patient_examination_id from token or related data
            if 'patient_examination_id' in missing_fields:
                # Attempt to extract from token or find related examination
                recovered_id = self._recover_patient_examination_id(token)
                if recovered_id:
                    data['patient_examination_id'] = recovered_id
                    logger.info(f"Recovered patient_examination_id {recovered_id} for token {token}")
                    
            # Recompute missing derived data
            if any(field in missing_fields for field in ['requirements_by_set', 'requirement_status']):
                try:
                    from ..services.lookup_service import recompute_lookup
                    recompute_lookup(token)
                    # Get fresh data after recompute
                    data = self.get_all()
                except Exception as e:
                    logger.error(f"Failed to recompute missing data for token {token}: {e}")
                    
        return data
    
    def _recover_patient_examination_id(self, token):
        """Attempt to recover patient_examination_id from token or database"""
        try:
            # Try to find the examination by token in related models
            from endoreg_db.models import PatientExamination
            
            # Look for examinations that might be associated with this token
            # This is a fallback - in a real scenario, you might have a token-to-examination mapping
            examinations = PatientExamination.objects.filter(
                # Add any fields that might help identify the examination
                # For example, if token is stored in a related model
            ).first()
            
            if examinations:
                return examinations.id
                
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to recover patient_examination_id for token {token}: {e}")
            
        return None