#!/usr/bin/env bash

# Endoreg-DB CLI helpers
#
# Usage:
#   source /home/admin/endoreg-db/scripts/endoreg_aliases.sh
#
# Optional environment variables:
#   ENDOREG_BASE_URL=http://localhost:8000/api
#   ENDOREG_ACCEPT_HEADER="Accept: application/json"
#   ENDOREG_AUTH_HEADER="Authorization: Bearer <token>"
#   ENDOREG_EXTRA_CURL_ARGS="--cookie sessionid=... --cookie csrftoken=..."

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Source this file instead of executing it:"
    echo "  source ${0}"
    exit 1
fi

ENDOREG_BASE_URL="${ENDOREG_BASE_URL:-http://localhost:8000/api}"
ENDOREG_ACCEPT_HEADER="${ENDOREG_ACCEPT_HEADER:-Accept: application/json}"
ENDOREG_AUTH_HEADER="${ENDOREG_AUTH_HEADER:-}"
ENDOREG_EXTRA_CURL_ARGS="${ENDOREG_EXTRA_CURL_ARGS:-}"

_endo_jq() {
    if command -v jq >/dev/null 2>&1; then
        jq
    else
        cat
    fi
}

_endo_curl() {
    local method="$1"
    local path="$2"
    shift 2

    local url="${ENDOREG_BASE_URL%/}/${path#/}"
    local args=(
        -sS
        -X "$method"
        -H "$ENDOREG_ACCEPT_HEADER"
    )

    if [[ -n "$ENDOREG_AUTH_HEADER" ]]; then
        args+=(-H "$ENDOREG_AUTH_HEADER")
    fi

    if [[ -n "$ENDOREG_EXTRA_CURL_ARGS" ]]; then
        # shellcheck disable=SC2206
        local extra_args=( $ENDOREG_EXTRA_CURL_ARGS )
        args+=("${extra_args[@]}")
    fi

    curl "${args[@]}" "$@" "$url"
}

endo_stats() {
    _endo_curl GET "media-management/status/" | _endo_jq
}

endo_clean_check() {
    _endo_curl DELETE "media-management/cleanup/?type=all&force=false" | _endo_jq
}

endo_clean_run() {
    _endo_curl DELETE "media-management/cleanup/?type=all&force=true" | _endo_jq
}

endo_rm() {
    if [[ -z "$1" ]]; then
        echo "Usage: endo_rm <file_id>"
        return 1
    fi
    _endo_curl DELETE "media-management/force-remove/$1/" | _endo_jq
}

endo_reset() {
    if [[ -z "$1" ]]; then
        echo "Usage: endo_reset <file_id>"
        return 1
    fi
    _endo_curl POST "media-management/reset-status/$1/" | _endo_jq
}

endo_video_reimport() {
    if [[ -z "$1" ]]; then
        echo "Usage: endo_video_reimport <video_id>"
        return 1
    fi
    _endo_curl POST "media/videos/$1/reimport/" | _endo_jq
}

endo_pdf_reimport() {
    if [[ -z "$1" ]]; then
        echo "Usage: endo_pdf_reimport <pdf_id>"
        return 1
    fi
    _endo_curl POST "media/pdfs/$1/reimport/" | _endo_jq
}

alias endo-stats='endo_stats'
alias endo-clean-check='endo_clean_check'
alias endo-clean-run='endo_clean_run'
alias endo-rm='endo_rm'
alias endo-reset='endo_reset'
alias endo-reimport='endo_reimport'
alias endo-pdf-reimport='endo_pdf_reimport'

echo "Endoreg aliases loaded:"
echo "  endo-stats"
echo "  endo-clean-check"
echo "  endo-clean-run"
echo "  endo-rm <file_id>"
echo "  endo-reset <file_id>"
echo "  endo-reimport <video_id>"
echo "  endo-pdf-reimport <pdf_id>"
