# Endoreg-db Agents.md

Case Generator and Requirements Module:

- Prefer yaml config
- Reference load_base_db_data

API and Integration

- No camelCase whatsoever
- LX-Annotate has automatic conversion as is expected from other api accessors.

Views best practise

We are currently using REST framework for the API.
All things video or report are located under the media/endpoint.

# Application purpose

This application will be run behind a proxy that adds api to all requests. Video streaming or other heavy tasks should be offloeaded to nginx if present