/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Strict -Xcheck:jni smoke consumer for the hosted Mesa llvmpipe + Xvfb
 * OpenGL ES 3.x JNI bridge control.
 */

public final class GlJniSmoke {
    private static native int[] renderCheckPixel(int r, int g, int b);

    private static boolean near(int actual, int expected) {
        return Math.abs(actual - expected) <= 2;
    }

    public static void main(String[] args) {
        if (args.length != 1) {
            throw new IllegalArgumentException("expected path to libtakx_gl_jni.so");
        }
        System.load(args[0]);
        final int targetR = 229;
        final int targetG = 160;
        final int targetB = 13;
        int[] px = renderCheckPixel(targetR, targetG, targetB);
        if (px == null || px.length != 4) {
            throw new IllegalStateException("native bridge returned invalid pixel array");
        }
        if (!near(px[0], targetR) || !near(px[1], targetG) || !near(px[2], targetB) || !near(px[3], 255)) {
            throw new IllegalStateException(
                "unexpected RGBA pixel from JNI GLES3 bridge: "
                    + px[0] + "," + px[1] + "," + px[2] + "," + px[3]
            );
        }
        System.out.println("pixel_jni=" + px[0] + "," + px[1] + "," + px[2] + "," + px[3]);
        System.out.println("jni_strict_gles3_bridge_readback=true");
    }
}
