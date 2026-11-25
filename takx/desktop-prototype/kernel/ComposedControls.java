// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
import com.atakmap.map.EngineLibrary;
import com.atakmap.map.layer.control.RenderContextControl;
import com.atakmap.map.layer.feature.style.TextureFillStyle;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Desktop prototype control for the composed Kernel 8.30.5 + project patches.
 * Loads the natives through the stock EngineLibrary resource loader only, then
 * calls real texture-fill and render-context JNI entry points. CPU and JNI
 * only: no GL context is created and nothing is rendered.
 */
public final class ComposedControls {
  static void check(boolean ok, String message) { if (!ok) throw new AssertionError(message); }

  public static void main(String[] args) throws Exception {
    check(args.length == 2, "usage: kernel-jar empty-manifest");
    Path jar = Path.of(args[0]).toRealPath();
    check(Path.of(EngineLibrary.class.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath().equals(jar),
        "EngineLibrary must originate in the composed JAR");
    check(Path.of(TextureFillStyle.class.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath().equals(jar),
        "TextureFillStyle must originate in the composed JAR");
    KernelRuntimeSmoke.initializeStrict();
    System.out.println("PASS_COMPOSED_STOCK_LOADER_INITIALIZED");

    // Texture: the unmodified takx/kernel-texture control. Its manifest argument
    // lists libraries to System.load; an empty manifest keeps the stock loader as
    // the only loader. It creates, clones, compares, packs and parses real native
    // TextureFillStyle objects and checks every field read back through JNI.
    check(Files.readAllLines(Path.of(args[1])).isEmpty(), "the texture control must not load any library itself");
    TextureStyleControl.main(new String[] {args[1]});
    Method nativeClass = TextureFillStyle.class.getDeclaredMethod("nativeClass");
    nativeClass.setAccessible(true);
    int texture = (Integer) nativeClass.invoke(null);
    TextureFillStyle style = new TextureFillStyle("file:///owned/desktop-prototype.png", 0xff102030, 0, 0, 16, 8, 0f, false);
    try {
      check(style.getWidth() == 16 && style.getHeight() == 8 && style.getColor() == 0xff102030, "texture fields");
    } finally { style.dispose(); }
    System.out.println("PASS_COMPOSED_TEXTURE_JNI nativeClass=" + texture);

    // Render context: the three natives the profile adds to GLMapView. Each one
    // validates its pointer first and reports TE_InvalidArg as a Java exception,
    // so a null peer is a defined call that needs no GL context. Reaching the
    // exception proves the symbol resolved and the native body ran.
    check(RenderContextControl.class.isInterface(), "RenderContextControl interface");
    Class<?> view = Class.forName("com.atakmap.map.opengl.GLMapView");
    Object[][] calls = {
        {"recreateContextCaches", new Class<?>[] {long.class}, new Object[] {0L}},
        {"releaseContextCaches", new Class<?>[] {long.class, boolean.class}, new Object[] {0L, Boolean.TRUE}},
        {"markContextTerminal", new Class<?>[] {long.class}, new Object[] {0L}}};
    for (Object[] call : calls) {
      Method method = view.getDeclaredMethod((String) call[0], (Class<?>[]) call[1]);
      check(java.lang.reflect.Modifier.isNative(method.getModifiers()), call[0] + " must be native");
      method.setAccessible(true);
      Throwable thrown = null;
      try { method.invoke(null, (Object[]) call[2]); }
      catch (InvocationTargetException error) { thrown = error.getCause(); }
      check(thrown != null, call[0] + " accepted a null peer");
      check(!(thrown instanceof LinkageError), call[0] + " did not resolve: " + thrown);
      System.out.println("RENDER_CONTEXT_JNI " + call[0] + " -> " + thrown.getClass().getName() + ": " + thrown.getMessage());
    }
    System.out.println("PASS_COMPOSED_RENDER_CONTEXT_JNI_NULL_PEER_REJECTED");
    KernelRuntimeSmoke.basic();
    System.out.println("PASS_COMPOSED_CONTROLS");
  }
}
