/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Real surfaceless EGL + OpenGL ES 3.x pixel-readback control linked directly
 * against the source-built libglvnd 1.7.0 dispatch libraries and executed on
 * Mesa llvmpipe.
 */

#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <link.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(expr)                                                                  \
    do {                                                                             \
        if (!(expr)) {                                                               \
            fprintf(stderr, "failed line %d: %s egl=0x%x gl=0x%x\n", __LINE__,       \
                    #expr, eglGetError(), glGetError());                             \
            return 2;                                                                \
        }                                                                            \
    } while (0)

static int list_provider(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size;
    (void)data;
    if (info->dlpi_name && info->dlpi_name[0]) {
        puts(info->dlpi_name);
    }
    return 0;
}

int main(void) {
    EGLDisplay display = eglGetPlatformDisplay(
        EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL
    );
    CHECK(display != EGL_NO_DISPLAY);

    EGLint major = 0, minor = 0;
    CHECK(eglInitialize(display, &major, &minor));
    CHECK(eglBindAPI(EGL_OPENGL_ES_API));

    const EGLint attrs[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT_KHR,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, 8,
        EGL_NONE
    };
    EGLConfig config = NULL;
    EGLint count = 0;
    CHECK(eglChooseConfig(display, attrs, &config, 1, &count) && count >= 1);

    const EGLint surf_attrs[] = {EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
    EGLSurface surface = eglCreatePbufferSurface(display, config, surf_attrs);
    CHECK(surface != EGL_NO_SURFACE);

    const EGLint ctx_attrs[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, ctx_attrs);
    CHECK(context != EGL_NO_CONTEXT);
    CHECK(eglMakeCurrent(display, surface, surface, context));

    GLint gl_major = 0;
    glGetIntegerv(GL_MAJOR_VERSION, &gl_major);
    CHECK(gl_major >= 3);

    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *version = (const char *)glGetString(GL_VERSION);
    CHECK(renderer != NULL && strstr(renderer, "llvmpipe") != NULL);
    CHECK(version != NULL && strstr(version, "OpenGL ES 3.") != NULL);

    glViewport(0, 0, 16, 16);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    CHECK(glGetError() == GL_NO_ERROR);

    unsigned char px[4] = {0, 0, 0, 0};
    glReadPixels(8, 8, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
    CHECK(glGetError() == GL_NO_ERROR);
    CHECK(abs((int)px[0] - 64) <= 1);
    CHECK(abs((int)px[1] - 128) <= 1);
    CHECK(abs((int)px[2] - 191) <= 1);
    CHECK(px[3] == 255);

    printf("egl=%d.%d\n", major, minor);
    printf("version=%s\n", version);
    printf("renderer=%s\n", renderer);
    printf("pixel=%u,%u,%u,%u\n", px[0], px[1], px[2], px[3]);
    puts("real_surfaceless_es3_clear_readback=true");
    puts("providers_begin");
    dl_iterate_phdr(list_provider, NULL);
    puts("providers_end");

    CHECK(eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT));
    CHECK(eglDestroyContext(display, context));
    CHECK(eglDestroySurface(display, surface));
    CHECK(eglTerminate(display));
    return 0;
}
