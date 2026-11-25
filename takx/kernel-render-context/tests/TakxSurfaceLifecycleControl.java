// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
import com.atakmap.map.EngineLibrary;
import com.atakmap.map.Globe;
import com.atakmap.map.LegacyAdapters;
import com.atakmap.map.MapRenderer3;
import com.atakmap.map.RenderContext;
import com.atakmap.map.RenderSurface;
import com.atakmap.map.elevation.ElevationSource;
import com.atakmap.map.elevation.ElevationSourceBuilder;
import com.atakmap.map.layer.control.ElevationSourceControl;
import com.atakmap.map.layer.control.RenderContextControl;
import com.atakmap.map.opengl.GLBaseMap;
import com.atakmap.map.opengl.GLMapView;
import com.atakmap.map.opengl.GLStarsRenderable;
import com.jogamp.opengl.GL;
import com.jogamp.opengl.GLAutoDrawable;
import com.jogamp.opengl.GLCapabilities;
import com.jogamp.opengl.GLContext;
import com.jogamp.opengl.GLEventListener;
import com.jogamp.opengl.GLProfile;
import com.jogamp.opengl.awt.GLCanvas;
import com.jogamp.opengl.awt.GLJPanel;
import gov.tak.api.engine.map.MapRenderer;
import gov.tak.api.engine.map.RenderSurface2;
import gov.tak.platform.commons.opengl.JOGLGLES;
import java.awt.BorderLayout;
import java.awt.Component;
import java.awt.EventQueue;
import java.awt.event.ComponentAdapter;
import java.awt.event.ComponentEvent;
import java.awt.event.ComponentListener;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import javax.swing.JFrame;

/**
 * Headed control for the client-owned render surface contract. It drives the
 * Kernel the way the official TAKX 5.7 client does (JOGLMapRendererSpi,
 * JOGLRenderContext, GlobePanel and GlobeCanvas of TAKX@0b59a9bb): the
 * surface is the client's own AWT component, the context keeps a plain
 * thread flag that only its own init/display callbacks set, and after JOGL
 * destroys the GL context the client creates a new context object inside
 * its init callback and hands it to RenderContextControl.
 *
 * The control needs a real display and GL implementation. It prints facts
 * and exits 0 only when every TAKX expectation and every rejection holds.
 * It does not qualify the renderer, the SDK or the client.
 *
 * Usage: TakxSurfaceLifecycleControl panel|canvas [recreations [last-frame.png]]
 */
public final class TakxSurfaceLifecycleControl {
    interface Surface extends GLAutoDrawable, RenderSurface, RenderSurface2 { int sizeListeners(); }

    /** TAKX GlobePanel/GlobeCanvas: the component itself is the render surface. */
    static final class Sizes {
        final Map<gov.tak.api.engine.map.RenderSurface.OnSizeChangedListener, ComponentListener> listeners = new IdentityHashMap<>();
        void add(Component component, Surface surface, gov.tak.api.engine.map.RenderSurface.OnSizeChangedListener l) {
            ComponentListener cl;
            synchronized (listeners) {
                if (listeners.containsKey(l)) return;
                cl = new ComponentAdapter() {
                    @Override public void componentResized(ComponentEvent e) {
                        l.onSizeChanged(surface, surface.getSurfaceWidth(), surface.getSurfaceHeight());
                    }
                };
                listeners.put(l, cl);
            }
            component.addComponentListener(cl);
        }
        void remove(Component component, gov.tak.api.engine.map.RenderSurface.OnSizeChangedListener l) {
            ComponentListener cl;
            synchronized (listeners) { cl = listeners.remove(l); }
            if (cl != null) component.removeComponentListener(cl);
        }
        int count() { synchronized (listeners) { return listeners.size(); } }
    }
    static final class Panel extends GLJPanel implements Surface {
        final Sizes sizes = new Sizes();
        Panel(GLCapabilities capabilities) { super(capabilities); }
        @Override public double getDpi() { return 96d; }
        @Override public int sizeListeners() { return sizes.count(); }
        @Override public void addOnSizeChangedListener(OnSizeChangedListener l) { sizes.add(this, this, l); }
        @Override public void removeOnSizeChangedListener(OnSizeChangedListener l) { sizes.remove(this, l); }
    }
    static final class Canvas extends GLCanvas implements Surface {
        final Sizes sizes = new Sizes();
        Canvas(GLCapabilities capabilities) { super(capabilities); }
        @Override public double getDpi() { return 96d; }
        @Override public int sizeListeners() { return sizes.count(); }
        @Override public void addOnSizeChangedListener(OnSizeChangedListener l) { sizes.add(this, this, l); }
        @Override public void removeOnSizeChangedListener(OnSizeChangedListener l) { sizes.remove(this, l); }
    }

    /** TAKX gov.takx.core.globe.tak.JOGLRenderContext, state for state. */
    static final class ClientContext implements RenderContext, GLEventListener {
        final GLAutoDrawable impl;
        final RenderSurface surface;
        Thread glThread;
        final LinkedList<Runnable> eventQueue = new LinkedList<>();
        ClientContext(Surface impl, boolean listen) {
            this.impl = impl;
            surface = impl;
            if (listen) impl.addGLEventListener(this);
        }
        @Override public boolean isRenderThread() { return Thread.currentThread() == glThread; }
        @Override public void queueEvent(Runnable r) {
            synchronized (eventQueue) { eventQueue.addLast(r); }
            ((Component) impl).repaint();
        }
        @Override public void requestRefresh() { ((Component) impl).repaint(); }
        @Override public void setFrameRate(float rate) {}
        @Override public float getFrameRate() { return 0; }
        @Override public void setContinuousRenderEnabled(boolean enabled) {}
        @Override public boolean isContinuousRenderEnabled() { return false; }
        @Override public boolean supportsChildContext() { return false; }
        @Override public RenderContext createChildContext() { return null; }
        @Override public void destroyChildContext(gov.tak.api.engine.map.RenderContext child) {}
        @Override public boolean isAttached() { return isRenderThread() && impl.getContext().isCurrent(); }
        @Override public boolean attach() {
            if (!isRenderThread()) return false;
            impl.getContext().makeCurrent();
            return impl.getContext().isCurrent();
        }
        @Override public boolean detach() {
            if (!isRenderThread()) return false;
            if (impl.getContext().isCurrent()) impl.getContext().release();
            return isAttached();
        }
        @Override public boolean isMainContext() { return true; }
        @Override public RenderSurface getRenderSurface() { return surface; }
        @Override public void init(GLAutoDrawable drawable) { glThread = Thread.currentThread(); }
        @Override public void dispose(GLAutoDrawable drawable) {}
        @Override public void display(GLAutoDrawable drawable) {
            glThread = Thread.currentThread();
            while (true) {
                Runnable r;
                synchronized (eventQueue) {
                    if (eventQueue.isEmpty()) break;
                    r = eventQueue.removeFirst();
                }
                try { r.run(); } catch (Throwable t) { fact("QUEUED_EVENT_FAILURE", t); }
            }
        }
        @Override public void reshape(GLAutoDrawable drawable, int x, int y, int width, int height) {}
    }

    static final List<String> failures = new ArrayList<>();
    static synchronized void fact(String name, Object value) { System.out.println(name + "\t" + value); System.out.flush(); }
    static synchronized void expect(boolean ok, String name) {
        fact(ok ? "OK" : "DEFECT", name);
        if (!ok) failures.add(name);
    }
    static String outcome(Runnable action) {
        try { action.run(); return "accepted"; }
        catch (RuntimeException error) { return error.getClass().getName() + ": " + error.getMessage(); }
    }

    static Surface component, otherComponent;
    static GLMapView view;
    static ClientContext context;
    static volatile int inits, displays, disposes, renderFailures, litFrames, queuedRuns;
    static volatile boolean destroyed;
    static volatile String lastInit = "", lastRender = "", glVersion = "";

    static void configure(GLMapView glMapView) {
        glMapView.setDisplayMode(MapRenderer3.DisplayMode.Globe);
        glMapView.setFocusPointOffset(0, 0);
        glMapView.setBaseMap(new GLBaseMap());
        glMapView.setStarsLayer(new GLStarsRenderable());
        ElevationSourceControl control = glMapView.getControl(ElevationSourceControl.class);
        ElevationSource multiplexSource = ElevationSourceBuilder.multiplex("Null Elevation Source", new ElevationSource[0]);
        control.setElevationSource(multiplexSource);
    }

    static void build(String kind) {
        GLCapabilities capabilities = new GLCapabilities(GLProfile.getMaxProgrammable(true));
        capabilities.setDepthBits(24);
        capabilities.setStencilBits(8);
        component = kind.equals("canvas") ? new Canvas(capabilities) : new Panel(capabilities);
        otherComponent = kind.equals("canvas") ? new Canvas(capabilities) : new Panel(capabilities);
        JFrame frame = new JFrame("render surface lifecycle control");
        frame.setLayout(new BorderLayout());
        frame.setSize(640, 480);
        context = new ClientContext(component, true);
        final ClientContext first = context;
        view = new GLMapView(context, new Globe(), 0, 0, ((Component) component).getWidth(), ((Component) component).getHeight());
        configure(view);
        MapRenderer<?> renderer = LegacyAdapters.adapt(view);

        // TAKX ApplicationGlobe.makeWwd: component = (Component) renderer.getRenderContext().getRenderSurface()
        Object exposed = renderer.getRenderContext().getRenderSurface();
        fact("SURFACE_CLASS_FROM_RENDER_CONTEXT", exposed.getClass().getName());
        expect(exposed == component, "renderer.getRenderContext().getRenderSurface() is the client component");
        expect(exposed instanceof Component, "the surface of the renderer context casts to java.awt.Component");
        // TAKX GLBeforeRenderingLayer, GLAbstractWorldWindLayer: (IWorldWindowGL) view.getRenderSurface()
        fact("SURFACE_CLASS_FROM_VIEW", view.getRenderSurface().getClass().getName());
        expect(view.getRenderSurface() == component, "GLMapView.getRenderSurface() is the client component");
        expect(view.getRenderSurface() instanceof GLAutoDrawable, "the surface of the view casts to the client's GLAutoDrawable type");

        component.addGLEventListener(new GLEventListener() {
            @Override public void init(GLAutoDrawable drawable) {
                JOGLGLES.init(drawable);
                drawable.getGL().setSwapInterval(0);
                glVersion = drawable.getGL().glGetString(GL.GL_VERSION);
                String result = "started";
                try {
                    if (destroyed) {
                        RenderContextControl control = view.getControl(RenderContextControl.class);
                        // Rejections first; none of them may change the renderer.
                        String foreign = outcome(() -> control.setRenderContext(new ClientContext(otherComponent, false)));
                        fact("REBIND_OTHER_SURFACE", foreign);
                        expect(foreign.startsWith("java.lang.IllegalArgumentException"),
                                "a replacement context with another surface object is rejected with IllegalArgumentException");
                        component.removeGLEventListener(first);
                        ClientContext replacement = new ClientContext(component, true);
                        fact("REPLACEMENT_FLAGS_AT_REBIND", "isRenderThread=" + replacement.isRenderThread()
                                + " drawableContextCurrent=" + (GLContext.getCurrent() != null && GLContext.getCurrent() == drawable.getContext()));
                        String rebound = outcome(() -> control.setRenderContext(replacement));
                        fact("REBIND_SAME_SURFACE_IN_INIT", rebound);
                        expect(rebound.equals("accepted"), "setRenderContext inside the client's init callback after GL context re-creation");
                        context = replacement;
                        configure(view);
                    }
                    view.start();
                } catch (Throwable error) { result = error.toString(); fact("INIT_FAILURE", error); }
                drawable.getGL().glClearColor(0f, 0f, 0f, 1f);
                lastInit = result;
                inits++;
            }
            @Override public void dispose(GLAutoDrawable drawable) {
                JOGLGLES.init(drawable);
                try { view.stop(); view.release(); }
                catch (Throwable error) { fact("DISPOSE_FAILURE", error); failures.add("dispose: " + error); }
                destroyed = true;
                disposes++;
            }
            @Override public void display(GLAutoDrawable drawable) {
                JOGLGLES.init(drawable);
                GL gl = drawable.getGL();
                gl.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT | GL.GL_STENCIL_BUFFER_BIT);
                try {
                    view.render();
                    lastRender = "rendered";
                    int w = drawable.getSurfaceWidth(), h = drawable.getSurfaceHeight();
                    if (w > 0 && h > 0) {
                        ByteBuffer pixels = ByteBuffer.allocateDirect(w * h * 4);
                        gl.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, pixels);
                        int lit = 0;
                        for (int i = 0; i < w * h * 4; i += 4)
                            if ((pixels.get(i) & 255) + (pixels.get(i + 1) & 255) + (pixels.get(i + 2) & 255) > 48) lit++;
                        if (lit * 20 > w * h) { litFrames++; lastFrame = pixels; lastFrameWidth = w; lastFrameHeight = h; }
                    }
                } catch (Throwable error) {
                    if (renderFailures++ == 0 || !error.toString().equals(lastRender)) fact("RENDER_FAILURE", error);
                    lastRender = error.toString();
                }
                displays++;
            }
            @Override public void reshape(GLAutoDrawable drawable, int x, int y, int width, int height) {}
        });
        frame.add((Component) component, BorderLayout.CENTER);
        frame.setVisible(true);
        TakxSurfaceLifecycleControl.frame = frame;
    }
    static JFrame frame;
    static volatile ByteBuffer lastFrame;
    static volatile int lastFrameWidth, lastFrameHeight;

    /** The last frame read back with glReadPixels, for a person to look at. */
    static void writeLastFrame(String path) throws Exception {
        ByteBuffer pixels = lastFrame;
        int w = lastFrameWidth, h = lastFrameHeight;
        if (pixels == null) return;
        java.awt.image.BufferedImage image = new java.awt.image.BufferedImage(w, h, java.awt.image.BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) {
                int i = (y * w + x) * 4;
                image.setRGB(x, h - 1 - y, (pixels.get(i) & 255) << 16 | (pixels.get(i + 1) & 255) << 8 | (pixels.get(i + 2) & 255));
            }
        javax.imageio.ImageIO.write(image, "png", new java.io.File(path));
        fact("LAST_FRAME", path + " " + w + "x" + h + " after " + inits + " GL contexts");
    }

    static void await(String what, java.util.function.BooleanSupplier done) throws Exception {
        long end = System.nanoTime() + 90_000_000_000L;
        while (!done.getAsBoolean()) {
            if (System.nanoTime() > end) { expect(false, "timed out waiting for " + what); return; }
            Thread.sleep(100);
            EventQueue.invokeLater(() -> ((Component) component).repaint());
        }
    }

    public static void main(String[] args) throws Exception {
        String kind = args.length > 0 ? args[0] : "panel";
        int recreations = args.length > 1 ? Integer.parseInt(args[1]) : 2;
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> { fact("UNCAUGHT", error); failures.add("uncaught: " + error); });
        try {
            run(kind, recreations);
            if (args.length > 2) writeLastFrame(args[2]);
        }
        catch (Throwable error) { fact("CONTROL_FAILURE", error); failures.add("control: " + error); }
        fact("SUMMARY", "kind=" + kind + " inits=" + inits + " disposes=" + disposes + " displays=" + displays + " litFrames=" + litFrames
                + " renderFailures=" + renderFailures + " defects=" + failures.size());
        for (String failure : failures) fact("DEFECT_LIST", failure);
        System.out.println(failures.isEmpty() ? "PASS_TAKX_SURFACE_LIFECYCLE_CONTROL " + kind : "FAIL_TAKX_SURFACE_LIFECYCLE_CONTROL " + kind);
        System.out.flush();
        Runtime.getRuntime().halt(failures.isEmpty() ? 0 : 1);
    }

    static void run(String kind, int recreations) throws Exception {
        EngineLibrary.initialize();
        EventQueue.invokeAndWait(() -> build(kind));
        await("the first frames", () -> displays >= 5 && litFrames >= 1 || renderFailures > 3);
        fact("GL_VERSION", glVersion);
        fact("FIRST_CONTEXT", "inits=" + inits + " displays=" + displays + " litFrames=" + litFrames + " renderFailures=" + renderFailures + " init=" + lastInit);
        expect(inits == 1 && lastInit.equals("started") && renderFailures == 0, "the first GL context initializes and renders without an exception");
        expect(litFrames >= 1, "the first GL context shows the globe (more than 5% of the pixels lit)");
        fact("SIZE_LISTENERS_WHILE_STARTED", component.sizeListeners());
        expect(component.sizeListeners() == 1, "the started renderer has exactly one size listener on the client component");
        for (int cycle = 1; cycle <= recreations; cycle++) {
            final int beforeInits = inits, beforeDisposes = disposes, beforeFailures = renderFailures;
            // Re-parenting destroys the GL context (GLCanvas/GLJPanel.removeNotify)
            // and the next addNotify/paint creates a new one.
            EventQueue.invokeAndWait(() -> { frame.remove((Component) component); frame.validate(); });
            await("dispose " + cycle, () -> disposes > beforeDisposes);
            expect(view.getControl(RenderContextControl.class) != null, "RenderContextControl is offered while released");
            expect(component.sizeListeners() == 0, "re-creation " + cycle + ": stop removed the renderer's size listener from the client component");
            // No GL context is current on this thread.
            String offThread = outcome(() -> view.getControl(RenderContextControl.class).setRenderContext(new ClientContext(component, false)));
            fact("REBIND_WITHOUT_CURRENT_CONTEXT", offThread);
            expect(offThread.startsWith("java.lang.IllegalStateException"), "setRenderContext without the GL context current is rejected with IllegalStateException");
            final int beforeDisplays = displays, beforeLit = litFrames;
            EventQueue.invokeAndWait(() -> { frame.add((Component) component, BorderLayout.CENTER); frame.validate(); });
            await("re-initialization " + cycle, () -> inits > beforeInits && (displays >= beforeDisplays + 5 && litFrames > beforeLit || renderFailures > beforeFailures + 3));
            fact("RECREATED_CONTEXT_" + cycle, "inits=" + inits + " disposes=" + disposes + " displays=" + (displays - beforeDisplays)
                    + " litFrames=" + (litFrames - beforeLit) + " renderFailures=" + (renderFailures - beforeFailures) + " init=" + lastInit + " render=" + lastRender);
            expect(lastInit.equals("started"), "re-creation " + cycle + ": the renderer is rebound and started in the init callback");
            expect(renderFailures == beforeFailures, "re-creation " + cycle + ": every frame after the rebind renders without an exception");
            expect(litFrames > beforeLit, "re-creation " + cycle + ": the globe is drawn again in the new GL context");
            expect(view.getRenderSurface() == component, "re-creation " + cycle + ": the view still exposes the client component");
            expect(component.sizeListeners() == 1, "re-creation " + cycle + ": the restarted renderer has exactly one size listener again");
            final int runs = queuedRuns;
            String queued = outcome(() -> view.queueEvent(() -> queuedRuns++));
            if (queued.equals("accepted")) await("queued work " + cycle, () -> queuedRuns > runs);
            else fact("QUEUE_AFTER_RECREATION", queued);
            expect(queuedRuns == runs + 1, "re-creation " + cycle + ": work queued through the view runs once in the new context");
            if (!lastInit.equals("started")) break; // the renderer is unusable; later cycles add nothing
        }
    }
}
