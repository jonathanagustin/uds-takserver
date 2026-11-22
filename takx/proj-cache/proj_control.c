/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX PROJ 4.9.1 static PIC archive.
 * Verifies PJ_VERSION, forward/inverse Spherical Mercator coordinate transforms,
 * and preserved Z height without using any host PROJ library.
 */

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "proj_api.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static int near_d(double actual, double expected, double tol) {
    return fabs(actual - expected) <= tol;
}

int main(void) {
    if (PJ_VERSION != 491) {
        fprintf(stderr, "unexpected PJ_VERSION: %d\n", PJ_VERSION);
        return 1;
    }

    const char *release = pj_get_release();
    if (release == NULL || strstr(release, "4.9.1") == NULL) {
        fprintf(stderr, "unexpected PROJ release: %s\n", release ? release : "(null)");
        return 1;
    }

    projPJ wgs84 = pj_init_plus("+proj=longlat +datum=WGS84 +no_defs");
    projPJ merc = pj_init_plus(
        "+proj=merc +a=6378137 +b=6378137 +lat_ts=0.0 +lon_0=0.0 "
        "+x_0=0.0 +y_0=0 +k=1.0 +units=m +nadgrids=@null +no_defs"
    );
    if (wgs84 == NULL || merc == NULL) {
        fprintf(stderr, "pj_init_plus failed\n");
        return 1;
    }

    const double cases[3][3] = {
        {0.0, 0.0, 125.5},
        {10.0, 45.0, 250.25},
        {-10.0, -45.0, -42.0},
    };
    const double expected_xy[3][2] = {
        {0.0, 0.0},
        {1113194.9079327357, 5621521.486192066},
        {-1113194.9079327357, -5621521.486192066},
    };

    for (int i = 0; i < 3; ++i) {
        double lon_rad = cases[i][0] * DEG_TO_RAD;
        double lat_rad = cases[i][1] * DEG_TO_RAD;
        double z = cases[i][2];

        double x = lon_rad;
        double y = lat_rad;
        double z_fwd = z;
        int rc = pj_transform(wgs84, merc, 1, 1, &x, &y, &z_fwd);
        if (rc != 0) {
            fprintf(stderr, "forward pj_transform failed (%d): %s\n", rc, pj_strerrno(rc));
            return 1;
        }
        if (!near_d(x, expected_xy[i][0], 1e-3) || !near_d(y, expected_xy[i][1], 1e-3) || !near_d(z_fwd, z, 1e-9)) {
            fprintf(stderr, "forward mismatch case %d: got (%.6f, %.6f, %.6f)\n", i, x, y, z_fwd);
            return 1;
        }

        double ix = x;
        double iy = y;
        double iz = z_fwd;
        rc = pj_transform(merc, wgs84, 1, 1, &ix, &iy, &iz);
        if (rc != 0) {
            fprintf(stderr, "inverse pj_transform failed (%d): %s\n", rc, pj_strerrno(rc));
            return 1;
        }
        if (!near_d(ix, lon_rad, 1e-9) || !near_d(iy, lat_rad, 1e-9) || !near_d(iz, z, 1e-9)) {
            fprintf(stderr, "inverse mismatch case %d: got (%.9f, %.9f, %.9f)\n", i, ix, iy, iz);
            return 1;
        }
    }

    pj_free(merc);
    pj_free(wgs84);

    printf("proj_version=%d\n", PJ_VERSION);
    printf("proj_release=%s\n", release);
    printf("proj_static_pic_control=true\n");
    return 0;
}
