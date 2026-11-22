/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX libcurl 8.23.0 static PIC archive
 * linked against prebuilt OpenSSL 3.5.9 and zlib 1.3.1.
 */

#include <stdio.h>
#include <string.h>

#include <curl/curl.h>

int main(void) {
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
        fprintf(stderr, "curl_global_init failed\n");
        return 1;
    }

    curl_version_info_data *info = curl_version_info(CURLVERSION_NOW);
    if (info == NULL || info->version == NULL || strstr(info->version, "8.23.0") == NULL) {
        fprintf(stderr, "unexpected curl version: %s\n", (info && info->version) ? info->version : "(null)");
        curl_global_cleanup();
        return 1;
    }
    if (info->ssl_version == NULL || strstr(info->ssl_version, "OpenSSL/3.5.9") == NULL) {
        fprintf(stderr, "unexpected curl SSL backend: %s\n", info->ssl_version ? info->ssl_version : "(null)");
        curl_global_cleanup();
        return 1;
    }
    if (info->libz_version == NULL || strcmp(info->libz_version, "1.3.1") != 0) {
        fprintf(stderr, "unexpected curl zlib backend: %s\n", info->libz_version ? info->libz_version : "(null)");
        curl_global_cleanup();
        return 1;
    }

    CURL *handle = curl_easy_init();
    if (handle == NULL) {
        fprintf(stderr, "curl_easy_init failed\n");
        curl_global_cleanup();
        return 1;
    }
    curl_easy_cleanup(handle);
    curl_global_cleanup();

    printf("curl_version=%s\n", info->version);
    printf("curl_ssl=%s\n", info->ssl_version);
    printf("curl_zlib=%s\n", info->libz_version);
    printf("curl_static_pic_control=true\n");
    return 0;
}
