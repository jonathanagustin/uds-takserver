/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 */

#include <proj_api.h>

#include <math.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    if (PJ_VERSION != 491 || strstr(pj_get_release(), "4.9.1") == NULL) {
        fprintf(stderr, "unexpected proj release: %s\n", pj_get_release());
        return 1;
    }

    projPJ geographic = pj_init_plus("+proj=longlat +datum=WGS84 +no_defs");
    projPJ mercator = pj_init_plus("+proj=merc +a=6378137 +b=6378137 +units=m +no_defs");
    if (!geographic || !mercator || !pj_is_latlong(geographic) || pj_is_latlong(mercator)) {
        fprintf(stderr, "pj_init_plus failed\n");
        if (geographic) {
            pj_free(geographic);
        }
        if (mercator) {
            pj_free(mercator);
        }
        return 1;
    }

    const double pi = 3.14159265358979323846;
    double x[3] = {0.0, 10.0 * pi / 180.0, -10.0 * pi / 180.0};
    double y[3] = {0.0, 45.0 * pi / 180.0, -45.0 * pi / 180.0};
    double z[3] = {12.5, 100.0, -7.0};

    if (pj_transform(geographic, mercator, 3, 1, x, y, z) != 0) {
        fprintf(stderr, "forward pj_transform failed\n");
        pj_free(geographic);
        pj_free(mercator);
        return 1;
    }

    const double expected_x[3] = {0.0, 1113194.9079327357, -1113194.9079327357};
    const double expected_y[3] = {0.0, 5621521.486192066, -5621521.486192066};
    for (int i = 0; i < 3; ++i) {
        if (fabs(x[i] - expected_x[i]) >= 1e-5 || fabs(y[i] - expected_y[i]) >= 1e-5) {
            fprintf(stderr, "forward projection mismatch at %d\n", i);
            pj_free(geographic);
            pj_free(mercator);
            return 1;
        }
    }

    if (pj_transform(mercator, geographic, 3, 1, x, y, z) != 0) {
        fprintf(stderr, "inverse pj_transform failed\n");
        pj_free(geographic);
        pj_free(mercator);
        return 1;
    }

    pj_free(geographic);
    pj_free(mercator);
    printf("proj_static_pic_control=true release=%s\n", pj_get_release());
    return 0;
}
