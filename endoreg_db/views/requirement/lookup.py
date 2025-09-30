# api/viewsets/lookup.py

from rest_framework import viewsets, status

from rest_framework.decorators import action

from rest_framework.response import Response


from rest_framework.parsers import JSONParser, FormParser, MultiPartParser


from endoreg_db.services.lookup_store import LookupStore, DEFAULT_TTL_SECONDS


# Use module import so tests can monkeypatch functions on the module


from endoreg_db.services import lookup_service as ls


from endoreg_db.utils.permissions import EnvironmentAwarePermission


from django.core.cache import cache


import logging


from ast import literal_eval


from collections.abc import Mapping





ORIGIN_MAP_PREFIX = "lookup:origin:"


ISSUED_MAP_PREFIX = "lookup:issued_for_internal:"





logger = logging.getLogger(__name__)



class LookupViewSet(viewsets.ViewSet):


    permission_classes = [EnvironmentAwarePermission]


    parser_classes = (JSONParser, FormParser, MultiPartParser)









    INPUT_KEYS = {"patient_examination_id", "selectedRequirementSetIds", "selectedChoices"}



    @action(detail=False, methods=["post"])

    def init(self, request):




        try:


            debug_data = getattr(request, 'data', None)


            raw_post = getattr(getattr(request, '_request', None), 'POST', None)


            body_preview = None


            try:


                body = getattr(getattr(request, '_request', None), 'body', b'')


                body_preview = body[:200]


            except Exception:


                body_preview = None


            logger.debug("lookup.init incoming: data=%r POST=%r body[:200]=%r", debug_data, raw_post, body_preview)


        except Exception:


            pass





        # Prefer DRF data


        raw_pe = request.data.get("patient_examination_id") if hasattr(request, "data") else None





        # Fallback: parse malformed form payload where the entire dict was sent as a single key string


        if raw_pe is None:


            for candidate in (getattr(getattr(request, '_request', None), 'POST', None), request.data if hasattr(request, 'data') else None):


                try:


                    if isinstance(candidate, Mapping) and len(candidate.keys()) == 1:


                        only_key = next(iter(candidate.keys()))


                        if isinstance(only_key, str) and only_key.startswith('{') and only_key.endswith('}'):


                            try:


                                parsed = literal_eval(only_key)


                                if isinstance(parsed, dict) and 'patient_examination_id' in parsed:


                                    raw_pe = parsed.get('patient_examination_id')


                                    logger.debug("lookup.init recovered pe_id from malformed payload: %r", raw_pe)


                                    break


                            except Exception:


                                pass


                except Exception:


                    pass





        # Fallback to query params


        if raw_pe is None:


            raw_pe = request.query_params.get("patient_examination_id")





        logger.debug("lookup.init raw_pe=%r type=%s", raw_pe, type(raw_pe))





        # Normalize potential list/tuple inputs (e.g., from form submissions)


        if isinstance(raw_pe, (list, tuple)):


            raw_pe = raw_pe[0] if raw_pe else None


        if raw_pe in (None, ""):


            return Response({"detail": "patient_examination_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)





        # Coerce to int robustly


        try:


            pe_id = int(str(raw_pe))


        except (TypeError, ValueError):


            logger.warning("lookup.init failed to int() raw_pe=%r", raw_pe)


            return Response({"detail": "patient_examination_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)


        if pe_id <= 0:


            return Response({"detail": "patient_examination_id must be positive"}, status=status.HTTP_400_BAD_REQUEST)





        try:


            # Create internal session via service (may seed its own token/cache)


            internal_token = ls.create_lookup_token_for_pe(pe_id)


            internal_data = LookupStore(token=internal_token).get_all()





            issued_key = f"{ISSUED_MAP_PREFIX}{internal_token}"


            issued_count = cache.get(issued_key, 0)





            if issued_count == 0:


                # First issuance: return the service token directly


                token_to_return = internal_token


                cache.set(issued_key, 1, DEFAULT_TTL_SECONDS)


            else:


                # Subsequent inits for same internal token: issue a fresh public token seeded with internal data


                public_store = LookupStore()


                token_to_return = public_store.init(initial=internal_data, ttl=DEFAULT_TTL_SECONDS)


                cache.set(issued_key, issued_count + 1, DEFAULT_TTL_SECONDS)





            # Persist origin mapping so we can restart expired sessions


            cache.set(f"{ORIGIN_MAP_PREFIX}{token_to_return}", pe_id, DEFAULT_TTL_SECONDS)

        except Exception as e:

            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


        return Response({"token": token_to_return}, status=status.HTTP_201_CREATED)



    @action(detail=True, methods=["get"], url_path="all")

    def get_all(self, request, pk=None):


        if not pk:


            return Response({"detail": "Token required"}, status=status.HTTP_404_NOT_FOUND)

        store = LookupStore(token=pk)


        try:


            validated_data = store.validate_and_recover_data(pk)


        except Exception:


            validated_data = None

        if validated_data is None:


            # Try automatic restart once using persisted origin mapping


            pe_id = cache.get(f"{ORIGIN_MAP_PREFIX}{pk}")


            if pe_id:


                try:


                    internal_token = ls.create_lookup_token_for_pe(int(pe_id))


                    new_data = LookupStore(token=internal_token).get_all()


                    if not new_data:


                        return Response({"error": "Lookup data not available after restart", "token": pk}, status=status.HTTP_404_NOT_FOUND)


                    return Response(new_data, status=status.HTTP_200_OK)


                except Exception:


                    pass


            return Response({"error": "Lookup data not found or expired", "token": pk}, status=status.HTTP_404_NOT_FOUND)

        return Response(store.get_all())



    @action(detail=True, methods=["get", "patch"], url_path="parts")

    def parts(self, request, pk=None):


        if not pk:


            return Response({"detail": "Token required"}, status=status.HTTP_404_NOT_FOUND)

        store = LookupStore(token=pk)



        if request.method == "GET":

            keys_param = request.query_params.get("keys", "")

            keys = [k.strip() for k in keys_param.split(",") if k.strip()]

            if not keys:

                return Response({"detail": "Provide ?keys=key1,key2"}, status=status.HTTP_400_BAD_REQUEST)


            try:


                return Response(store.get_many(keys))


            except Exception:


                return Response({"detail": "Lookup data not found or expired"}, status=status.HTTP_404_NOT_FOUND)



        # PATCH

        updates = request.data.get("updates", {})


        if not isinstance(updates, dict) or not updates:


            return Response({"detail": "updates must be a non-empty object"}, status=status.HTTP_400_BAD_REQUEST)



        store.set_many(updates)



        if any(key in self.INPUT_KEYS for key in updates.keys()):

            try:


                ls.recompute_lookup(pk)

            except Exception as e:

                import logging


                logging.getLogger(__name__).error("Failed to recompute after patch for token %s: %s", pk, e)



        return Response({"ok": True, "token": pk}, status=status.HTTP_200_OK)



    @action(detail=True, methods=["post"], url_path="recompute")

    def recompute(self, request, pk=None):

        """Recompute lookup data based on current PatientExamination and user selections"""


        if not pk:


            return Response({"detail": "Token required"}, status=status.HTTP_404_NOT_FOUND)

        try:


            updates = ls.recompute_lookup(pk)


            return Response({"ok": True, "token": pk, "updates": updates}, status=status.HTTP_200_OK)





        except ValueError as e:

            return Response({"detail": str(e)}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:

            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)