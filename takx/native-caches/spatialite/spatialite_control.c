/* Copyright 2026 uds-takserver authors */
/* SPDX-License-Identifier: GPL-3.0-only */

#include <sqlite3.h>
#include <spatialite.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  const char *ver = spatialite_version();
  if (ver == NULL || strncmp(ver, "4.3.0a", 6) != 0) {
    fprintf(stderr, "unexpected spatialite_version: %s\n", ver ? ver : "<null>");
    return 1;
  }

  sqlite3 *db = NULL;
  if (sqlite3_open(":memory:", &db) != SQLITE_OK || db == NULL) {
    fprintf(stderr, "sqlite3_open failed\n");
    return 1;
  }

  void *cache = spatialite_alloc_connection();
  if (cache == NULL) {
    fprintf(stderr, "spatialite_alloc_connection failed\n");
    sqlite3_close(db);
    return 1;
  }
  spatialite_init_ex(db, cache, 0);

  sqlite3_stmt *stmt = NULL;
  const char *sql =
      "SELECT ST_GeometryType(ST_Buffer(GeomFromText('POINT(10 20)', 4326), 1.0)), "
      "ST_Area(ST_Buffer(GeomFromText('POINT(0 0)', 4326), 2.0));";
  if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) != SQLITE_OK) {
    fprintf(stderr, "sqlite3_prepare_v2 failed: %s\n", sqlite3_errmsg(db));
    sqlite3_close(db);
    spatialite_cleanup_ex(cache);
    spatialite_shutdown();
    return 1;
  }

  int rc = sqlite3_step(stmt);
  if (rc != SQLITE_ROW) {
    fprintf(stderr, "sqlite3_step failed: %d (%s)\n", rc, sqlite3_errmsg(db));
    sqlite3_finalize(stmt);
    sqlite3_close(db);
    spatialite_cleanup_ex(cache);
    spatialite_shutdown();
    return 1;
  }

  const unsigned char *geom_type = sqlite3_column_text(stmt, 0);
  double area = sqlite3_column_double(stmt, 1);
  if (geom_type == NULL || strcmp((const char *)geom_type, "POLYGON") != 0 ||
      area < 12.0 || area > 13.0) {
    fprintf(stderr, "unexpected geometry result: type=%s area=%f\n",
            geom_type ? (const char *)geom_type : "<null>", area);
    sqlite3_finalize(stmt);
    sqlite3_close(db);
    spatialite_cleanup_ex(cache);
    spatialite_shutdown();
    return 1;
  }

  sqlite3_finalize(stmt);
  sqlite3_close(db);
  spatialite_cleanup_ex(cache);
  spatialite_shutdown();

  printf("spatialite_version=%s\n", ver);
  printf("spatialite_static_pic_control=true\n");
  return 0;
}
