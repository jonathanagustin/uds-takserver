/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX OpenSSL 3.5.9 LTS static PIC archives.
 * Verifies exact runtime version string and NIST FIPS 197 AES-128-ECB vector via EVP.
 */

#include <stdio.h>
#include <string.h>

#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/ssl.h>

int main(void) {
    const char *ver = OpenSSL_version(OPENSSL_VERSION);
    if (ver == NULL || strstr(ver, "3.5.9") == NULL) {
        fprintf(stderr, "unexpected OpenSSL version: %s\n", ver ? ver : "(null)");
        return 1;
    }

    const unsigned char key[16] = {
        0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6,
        0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c
    };
    const unsigned char pt[16] = {
        0x32, 0x43, 0xf6, 0xa8, 0x88, 0x5a, 0x30, 0x8d,
        0x31, 0x31, 0x98, 0xa2, 0xe0, 0x37, 0x07, 0x34
    };
    const unsigned char expected_ct[16] = {
        0x39, 0x25, 0x84, 0x1d, 0x02, 0xdc, 0x09, 0xfb,
        0xdc, 0x11, 0x85, 0x97, 0x19, 0x6a, 0x0b, 0x32
    };

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    unsigned char ct[32] = {0};
    int len = 0, total = 0;
    if (!ctx ||
        EVP_EncryptInit_ex(ctx, EVP_aes_128_ecb(), NULL, key, NULL) != 1 ||
        EVP_CIPHER_CTX_set_padding(ctx, 0) != 1 ||
        EVP_EncryptUpdate(ctx, ct, &len, pt, 16) != 1) {
        fprintf(stderr, "EVP AES-128-ECB encrypt failed\n");
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    total = len;
    if (EVP_EncryptFinal_ex(ctx, ct + total, &len) != 1) {
        fprintf(stderr, "EVP AES-128-ECB final failed\n");
        EVP_CIPHER_CTX_free(ctx);
        return 1;
    }
    total += len;
    EVP_CIPHER_CTX_free(ctx);

    if (total != 16 || memcmp(ct, expected_ct, 16) != 0) {
        fprintf(stderr, "NIST AES-128-ECB vector mismatch\n");
        return 1;
    }

    SSL_CTX *ssl_ctx = SSL_CTX_new(TLS_client_method());
    if (ssl_ctx == NULL) {
        fprintf(stderr, "SSL_CTX_new failed\n");
        return 1;
    }
    SSL_CTX_free(ssl_ctx);

    printf("openssl_version=%s\n", ver);
    printf("openssl_static_pic_control=true\n");
    return 0;
}
