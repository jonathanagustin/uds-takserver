/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only */
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <X11/Xlib.h>
#include <link.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(x) do { \
    if (!(x)) { \
        fprintf(stderr, "failed line %d: %s egl=0x%x gl=0x%x\n", \
                __LINE__, #x, eglGetError(), glGetError()); \
        exit(2); \
    } \
} while (0)

static int list_provider(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size;
    (void)data;
    if (info->dlpi_name && info->dlpi_name[0]) {
        puts(info->dlpi_name);
    }
    return 0;
}

static GLuint compile_shader(GLenum type, const char *src) {
    GLuint shader = glCreateShader(type);
    CHECK(shader != 0);
    glShaderSource(shader, 1, &src, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetShaderInfoLog(shader, sizeof(log), NULL, log);
        fprintf(stderr, "shader compile error: %s\n", log);
        exit(3);
    }
    return shader;
}

static GLuint link_program(const char *vs_src, const char *fs_src) {
    GLuint vs = compile_shader(GL_VERTEX_SHADER, vs_src);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, fs_src);
    GLuint prog = glCreateProgram();
    CHECK(prog != 0);
    glAttachShader(prog, vs);
    glAttachShader(prog, fs);
    glLinkProgram(prog);
    GLint ok = 0;
    glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetProgramInfoLog(prog, sizeof(log), NULL, log);
        fprintf(stderr, "program link error: %s\n", log);
        exit(4);
    }
    glDeleteShader(vs);
    glDeleteShader(fs);
    return prog;
}

static void read_center_pixel(int width, int height, unsigned char out[4]) {
    glFinish();
    CHECK(glGetError() == GL_NO_ERROR);
    glReadPixels(width / 2, height / 2, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, out);
    CHECK(glGetError() == GL_NO_ERROR);
}

static int near_u8(unsigned char actual, int expected) {
    return abs((int)actual - expected) <= 2;
}

int main(void) {
    const char *display_env = getenv("DISPLAY");
    CHECK(display_env != NULL && display_env[0] != '\0');

    Display *xdisplay = XOpenDisplay(NULL);
    CHECK(xdisplay != NULL);

    Window root = DefaultRootWindow(xdisplay);
    const int width = 64;
    const int height = 64;
    Window xwin = XCreateSimpleWindow(xdisplay, root, 0, 0, width, height, 0, 0, 0);
    CHECK(xwin != 0);
    XMapWindow(xdisplay, xwin);
    XSync(xdisplay, False);

    EGLDisplay edisplay = eglGetDisplay((EGLNativeDisplayType)xdisplay);
    CHECK(edisplay != EGL_NO_DISPLAY);
    EGLint major = 0, minor = 0;
    CHECK(eglInitialize(edisplay, &major, &minor));
    CHECK(eglBindAPI(EGL_OPENGL_ES_API));

    EGLint cfg_attrs[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, 8,
        EGL_NONE
    };
    EGLConfig config = NULL;
    EGLint count = 0;
    CHECK(eglChooseConfig(edisplay, cfg_attrs, &config, 1, &count) && count == 1);

    EGLSurface surface = eglCreateWindowSurface(edisplay, config, (EGLNativeWindowType)xwin, NULL);
    CHECK(surface != EGL_NO_SURFACE);

    EGLint ctx_attrs[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
    EGLContext ctx1 = eglCreateContext(edisplay, config, EGL_NO_CONTEXT, ctx_attrs);
    CHECK(ctx1 != EGL_NO_CONTEXT);
    CHECK(eglMakeCurrent(edisplay, surface, surface, ctx1));

    GLint glmajor = 0;
    glGetIntegerv(GL_MAJOR_VERSION, &glmajor);
    CHECK(glmajor >= 3);
    const char *renderer_ptr = (const char *)glGetString(GL_RENDERER);
    const char *version_ptr = (const char *)glGetString(GL_VERSION);
    CHECK(renderer_ptr && strstr(renderer_ptr, "llvmpipe") && version_ptr && strstr(version_ptr, "OpenGL ES 3."));
    char renderer[256];
    char version[256];
    snprintf(renderer, sizeof(renderer), "%s", renderer_ptr);
    snprintf(version, sizeof(version), "%s", version_ptr);

    /* Control 1: Xvfb ES 3 clear + readback. */
    glViewport(0, 0, width, height);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    unsigned char px1[4] = {0};
    read_center_pixel(width, height, px1);
    CHECK(near_u8(px1[0], 64) && near_u8(px1[1], 128) && near_u8(px1[2], 191) && px1[3] == 255);

    /* Control 2: Textured polygon atlas sampling (#498 GL path). */
    const char *tex_vs =
        "#version 300 es\n"
        "layout(location = 0) in vec2 aPos;\n"
        "layout(location = 1) in vec2 aUv;\n"
        "out vec2 vUv;\n"
        "void main() { vUv = aUv; gl_Position = vec4(aPos, 0.0, 1.0); }\n";
    const char *tex_fs =
        "#version 300 es\n"
        "precision mediump float;\n"
        "uniform sampler2D uAtlas;\n"
        "in vec2 vUv;\n"
        "out vec4 oColor;\n"
        "void main() { oColor = texture(uAtlas, vUv); }\n";
    GLuint tex_prog = link_program(tex_vs, tex_fs);
    glUseProgram(tex_prog);

    /* 2x2 RGBA atlas: (0,0)=red, (1,0)=green, (0,1)=blue, (1,1)=signal amber (229,160,13,255). */
    const unsigned char atlas[16] = {
        220,  30,  30, 255,
         30, 210,  90, 255,
         40, 100, 230, 255,
        229, 160,  13, 255
    };
    GLuint tex_id = 0;
    glGenTextures(1, &tex_id);
    CHECK(tex_id != 0);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex_id);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 2, 2, 0, GL_RGBA, GL_UNSIGNED_BYTE, atlas);
    CHECK(glGetError() == GL_NO_ERROR);

    /* Sample strictly inside top-right quadrant (1,1) -> signal amber. */
    const float quad[] = {
        -1.0f, -1.0f, 0.75f, 0.75f,
         1.0f, -1.0f, 0.75f, 0.75f,
        -1.0f,  1.0f, 0.75f, 0.75f,
         1.0f,  1.0f, 0.75f, 0.75f
    };
    GLuint vao = 0, vbo = 0;
    glGenVertexArrays(1, &vao);
    glGenBuffers(1, &vbo);
    glBindVertexArray(vao);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(quad), quad, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (const void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (const void *)(2 * sizeof(float)));
    glUniform1i(glGetUniformLocation(tex_prog, "uAtlas"), 0);

    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    unsigned char px2[4] = {0};
    read_center_pixel(width, height, px2);
    CHECK(near_u8(px2[0], 229) && near_u8(px2[1], 160) && near_u8(px2[2], 13) && px2[3] == 255);

    /* Control 3: Context destroy, handle invalidation, and clean recreation (#499 GL path). */
    GLuint stale_tex = tex_id;
    GLuint stale_prog = tex_prog;
    CHECK(glIsTexture(stale_tex) == GL_TRUE);
    CHECK(glIsProgram(stale_prog) == GL_TRUE);

    CHECK(eglMakeCurrent(edisplay, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT));
    CHECK(eglDestroyContext(edisplay, ctx1));

    EGLContext ctx2 = eglCreateContext(edisplay, config, EGL_NO_CONTEXT, ctx_attrs);
    CHECK(ctx2 != EGL_NO_CONTEXT);
    CHECK(eglMakeCurrent(edisplay, surface, surface, ctx2));
    CHECK(glIsTexture(stale_tex) == GL_FALSE);
    CHECK(glIsProgram(stale_prog) == GL_FALSE);

    glViewport(0, 0, width, height);
    glClearColor(0.20f, 0.83f, 0.60f, 1.0f); /* emerald recovery check */
    glClear(GL_COLOR_BUFFER_BIT);
    unsigned char px3[4] = {0};
    read_center_pixel(width, height, px3);
    CHECK(near_u8(px3[0], 51) && near_u8(px3[1], 212) && near_u8(px3[2], 153) && px3[3] == 255);

    /* Control 4: Layer visibility toggle with zero stale pixels (#589 GL path). */
    const char *solid_vs =
        "#version 300 es\n"
        "layout(location = 0) in vec2 aPos;\n"
        "void main() { gl_Position = vec4(aPos, 0.0, 1.0); }\n";
    const char *solid_fs =
        "#version 300 es\n"
        "precision mediump float;\n"
        "out vec4 oColor;\n"
        "void main() { oColor = vec4(0.90, 0.63, 0.05, 1.0); }\n";
    GLuint solid_prog = link_program(solid_vs, solid_fs);
    const float layer_tri[] = { -0.8f, -0.8f, 0.8f, -0.8f, 0.0f, 0.8f };
    GLuint vao2 = 0, vbo2 = 0;
    glGenVertexArrays(1, &vao2);
    glGenBuffers(1, &vbo2);
    glBindVertexArray(vao2);
    glBindBuffer(GL_ARRAY_BUFFER, vbo2);
    glBufferData(GL_ARRAY_BUFFER, sizeof(layer_tri), layer_tri, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * sizeof(float), (const void *)0);

    /* Pass A: layer visible. */
    glClearColor(0.04f, 0.05f, 0.07f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glUseProgram(solid_prog);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    unsigned char px4_on[4] = {0};
    read_center_pixel(width, height, px4_on);
    CHECK(near_u8(px4_on[0], 230) && near_u8(px4_on[1], 161) && near_u8(px4_on[2], 13));

    /* Pass B: layer hidden -> clear and redraw without geometry; zero stale pixels. */
    glClearColor(0.04f, 0.05f, 0.07f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    unsigned char px4_off[4] = {0};
    read_center_pixel(width, height, px4_off);
    CHECK(near_u8(px4_off[0], 10) && near_u8(px4_off[1], 13) && near_u8(px4_off[2], 18) && px4_off[3] == 255);

    glDeleteBuffers(1, &vbo2);
    glDeleteVertexArrays(1, &vao2);
    glDeleteProgram(solid_prog);

    printf("display=%s\n", display_env);
    printf("egl=%d.%d\n", major, minor);
    printf("version=%s\n", version);
    printf("renderer=%s\n", renderer);
    printf("xvfb_es3_clear_readback=true\n");
    printf("textured_polygon_atlas_readback=true\n");
    printf("context_destroy_recreate_invalidation=true\n");
    printf("layer_visibility_clear_redraw=true\n");
    printf("pixel_clear=%u,%u,%u,%u\n", px1[0], px1[1], px1[2], px1[3]);
    printf("pixel_atlas=%u,%u,%u,%u\n", px2[0], px2[1], px2[2], px2[3]);
    printf("pixel_recreated=%u,%u,%u,%u\n", px3[0], px3[1], px3[2], px3[3]);
    printf("pixel_layer_on=%u,%u,%u,%u\n", px4_on[0], px4_on[1], px4_on[2], px4_on[3]);
    printf("pixel_layer_off=%u,%u,%u,%u\n", px4_off[0], px4_off[1], px4_off[2], px4_off[3]);
    puts("providers_begin");
    dl_iterate_phdr(list_provider, NULL);
    puts("providers_end");

    CHECK(eglMakeCurrent(edisplay, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT));
    CHECK(eglDestroyContext(edisplay, ctx2));
    CHECK(eglDestroySurface(edisplay, surface));
    CHECK(eglTerminate(edisplay));
    XDestroyWindow(xdisplay, xwin);
    XCloseDisplay(xdisplay);
    return 0;
}
