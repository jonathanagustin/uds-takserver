/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX Assimp 4.0.1 static PIC archive
 * linked against prebuilt zlib 1.3.1. Imports an in-memory OBJ triangle and
 * verifies version, mesh count, and vertex count.
 */

#include <stdio.h>
#include <string.h>

#include <assimp/cimport.h>
#include <assimp/postprocess.h>
#include <assimp/scene.h>
#include <assimp/version.h>

int main(void) {
    unsigned int major = aiGetVersionMajor();
    unsigned int minor = aiGetVersionMinor();
    if (major != 4 || minor != 0) {
        fprintf(stderr, "unexpected Assimp version: %u.%u\n", major, minor);
        return 1;
    }

    const char *obj =
        "v 0.0 0.0 0.0\n"
        "v 1.0 0.0 0.0\n"
        "v 0.0 1.0 0.0\n"
        "f 1 2 3\n";
    const struct aiScene *scene = aiImportFileFromMemory(
        obj, (unsigned int)strlen(obj), aiProcess_Triangulate, "obj"
    );
    if (scene == NULL || scene->mNumMeshes != 1 || scene->mMeshes[0]->mNumVertices != 3) {
        fprintf(stderr, "assimp OBJ import control failed\n");
        if (scene != NULL) {
            aiReleaseImport(scene);
        }
        return 1;
    }
    aiReleaseImport(scene);

    printf("assimp_version=%u.%u.%u\n", major, minor, aiGetVersionRevision());
    printf("assimp_static_pic_control=true\n");
    return 0;
}
