/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Strict JNI native bridge for hosted Mesa llvmpipe + Xvfb OpenGL ES 3.x
 * qualification. Compiled as libtakx_gl_jni.so and loaded under java -Xcheck:jni.
 */

#include <jni.h>
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <X11/Xlib.h>

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void throw_runtime(JNIEnv *env, const char *message) {
    jclass ex = (*env)->FindClass(env, "java/lang/RuntimeException");
    if (ex != NULL) {
        (*env)->ThrowNew(env, ex, message);
        (*env)->DeleteLocalRef(env, ex);
    }
}

JNIEXPORT jintArray JNICALL Java_GlJniSmoke_renderCheckPixel(
    JNIEnv *env, jclass cls, jint r, jint g, jint b
) {
    (void)cls;

    Display *xdisplay = XOpenDisplay(NULL);
    if (xdisplay == NULL) {
        throw_runtime(env, "XOpenDisplay failed under Xvfb");
        return NULL;
    }

    int screen = DefaultScreen(xdisplay);
    Window root = RootWindow(xdisplay, screen);
    Window win = XCreateSimpleWindow(xdisplay, root, 0, 0, 64, 64, 0, 0, 0);
    XMapWindow(xdisplay, win);
    XSync(xdisplay, False);

    EGLDisplay display = eglGetDisplay((EGLNativeDisplayType)xdisplay);
    EGLSurface surface = EGL_NO_SURFACE;
    EGLContext ctx = EGL_NO_CONTEXT;
    const char *err = NULL;
    uint8_t px[4] = {0, 0, 0, 0};

    if (display == EGL_NO_DISPLAY) {
        err = "eglGetDisplay failed";
        goto cleanup;
    }
    EGLint major = 0, minor = 0;
    if (!eglInitialize(display, &major, &minor)) {
        err = "eglInitialize failed";
        goto cleanup;
    }
    if (!eglBindAPI(EGL_OPENGL_ES_API)) {
        err = "eglBindAPI failed";
        goto cleanup;
    }

    const EGLint cfg_attrs[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, 8,
        EGL_NONE
    };
    EGLConfig cfg = NULL;
    EGLint num_cfg = 0;
    if (!eglChooseConfig(display, cfg_attrs, &cfg, 1, &num_cfg) || num_cfg < 1) {
        err = "eglChooseConfig failed";
        goto cleanup;
    }

    surface = eglCreateWindowSurface(display, cfg, (EGLNativeWindowType)win, NULL);
    if (surface == EGL_NO_SURFACE) {
        err = "eglCreateWindowSurface failed";
        goto cleanup;
    }

    const EGLint ctx_attrs[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    ctx = eglCreateContext(display, cfg, EGL_NO_CONTEXT, ctx_attrs);
    if (ctx == EGL_NO_CONTEXT) {
        err = "eglCreateContext failed";
        goto cleanup;
    }

    if (!eglMakeCurrent(display, surface, surface, ctx)) {
        err = "eglMakeCurrent failed";
        goto cleanup;
    }

    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *version = (const char *)glGetString(GL_VERSION);
    if (renderer == NULL || strstr(renderer, "llvmpipe") == NULL) {
        err = "expected Mesa llvmpipe renderer in JNI bridge";
        goto cleanup;
    }
    if (version == NULL || strstr(version, "OpenGL ES 3.") == NULL) {
        err = "expected OpenGL ES 3.x version in JNI bridge";
        goto cleanup;
    }

    glViewport(0, 0, 64, 64);
    glClearColor((GLfloat)r / 255.0f, (GLfloat)g / 255.0f, (GLfloat)b / 255.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    glReadPixels(32, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);

cleanup:
    if (display != EGL_NO_DISPLAY) {
        eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (ctx != EGL_NO_CONTEXT) {
            eglDestroyContext(display, ctx);
        }
        if (surface != EGL_NO_SURFACE) {
            eglDestroySurface(display, surface);
        }
        eglTerminate(display);
    }
    XDestroyWindow(xdisplay, win);
    XCloseDisplay(xdisplay);

    if (err != NULL) {
        throw_runtime(env, err);
        return NULL;
    }

    jintArray out = (*env)->NewIntArray(env, 4);
    if (out == NULL) {
        return NULL;
    }
    jint vals[4] = {(jint)px[0], (jint)px[1], (jint)px[2], (jint)px[3]};
    (*env)->SetIntArrayRegion(env, out, 0, 4, vals);
    return out;
}
