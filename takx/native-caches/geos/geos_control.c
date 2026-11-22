/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 */

#include <geos_c.h>

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_errors = 0;

static void notice_handler(const char *fmt, ...) {
    (void)fmt;
}

static void error_handler(const char *fmt, ...) {
    (void)fmt;
    g_errors++;
}

int main(void) {
    GEOSContextHandle_t ctx = initGEOS_r(notice_handler, error_handler);
    if (ctx == NULL || strncmp(GEOSversion(), "3.4.3", 5) != 0) {
        fprintf(stderr, "unexpected geos version: %s\n", GEOSversion());
        return 1;
    }

    GEOSGeometry *a = GEOSGeomFromWKT_r(ctx, "POLYGON((0 0,4 0,4 4,0 4,0 0))");
    GEOSGeometry *b = GEOSGeomFromWKT_r(ctx, "POLYGON((2 2,6 2,6 6,2 6,2 2))");
    GEOSGeometry *p = GEOSGeomFromWKT_r(ctx, "POINT(7 4)");
    if (!a || !b || !p) {
        fprintf(stderr, "GEOSGeomFromWKT_r failed\n");
        finishGEOS_r(ctx);
        return 1;
    }

    GEOSGeometry *intersection = GEOSIntersection_r(ctx, a, b);
    GEOSGeometry *buffer = GEOSBuffer_r(ctx, p, 2.0, 32);
    double area = 0.0;
    double distance = 0.0;
    double buffered = 0.0;
    int ok = intersection && buffer &&
             GEOSArea_r(ctx, intersection, &area) && fabs(area - 4.0) < 1e-12 &&
             GEOSDistance_r(ctx, a, p, &distance) && fabs(distance - 3.0) < 1e-12 &&
             GEOSisValid_r(ctx, buffer) == 1 &&
             GEOSArea_r(ctx, buffer, &buffered) && fabs(buffered - 4.0 * acos(-1.0)) < 0.01 &&
             g_errors == 0;

    GEOSGeometry *bad = GEOSGeomFromWKT_r(ctx, "POINT(broken)");
    if (bad != NULL || g_errors == 0) {
        ok = 0;
    }

    if (intersection) {
        GEOSGeom_destroy_r(ctx, intersection);
    }
    if (buffer) {
        GEOSGeom_destroy_r(ctx, buffer);
    }
    GEOSGeom_destroy_r(ctx, a);
    GEOSGeom_destroy_r(ctx, b);
    GEOSGeom_destroy_r(ctx, p);
    finishGEOS_r(ctx);

    if (!ok) {
        fprintf(stderr, "geos geometry control failed\n");
        return 1;
    }

    printf("geos_static_pic_control=true version=%s\n", GEOSversion());
    return 0;
}
