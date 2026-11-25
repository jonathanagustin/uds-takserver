// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.opengl;

import gov.tak.api.engine.map.RenderContext;
import gov.tak.api.engine.map.RenderSurface;
import java.util.ArrayList;
import java.util.List;

/** State-machine unit test only. This does not qualify a GL implementation. */
public final class BindingStateTest {
    private static int assertions;
    private static void check(boolean value) {
        assertions++;
        if (!value) throw new AssertionError(assertions);
    }
    private static void rejects(Runnable action) {
        try { action.run(); throw new AssertionError("accepted illegal transition"); }
        catch (IllegalStateException expected) { assertions++; }
    }
    static class Context implements RenderContext<RenderSurface> {
        final List<Runnable> queued = new ArrayList<>();
        boolean current = true;
        final Thread owningThread=Thread.currentThread();
        final RenderSurface surface = new RenderSurface() {
            public double getDpi() { return 96; }
            public int getWidth() { return 32; }
            public int getHeight() { return 24; }
            public void addOnSizeChangedListener(OnSizeChangedListener listener) {}
            public void removeOnSizeChangedListener(OnSizeChangedListener listener) {}
        };
        public boolean isRenderThread() { return current && Thread.currentThread()==owningThread; }
        public void queueEvent(Runnable task) { queued.add(task); }
        public void requestRefresh() {}
        public void setFrameRate(float value) {}
        public float getFrameRate() { return 0; }
        public void setContinuousRenderEnabled(boolean value) {}
        public boolean isContinuousRenderEnabled() { return false; }
        public boolean supportsChildContext() { return false; }
        public RenderContext createChildContext() { throw new UnsupportedOperationException(); }
        public void destroyChildContext(RenderContext child) { throw new UnsupportedOperationException(); }
        public boolean isAttached() { return isRenderThread(); }
        public boolean attach() { return current; }
        public boolean detach() { return false; }
        public boolean isMainContext() { return true; }
        public RenderSurface getRenderSurface() { return surface; }
    }
    static final class SubmissionContext extends Context {
        Throwable failure;
        boolean inline, enqueueBeforeFailure;
        @Override public void queueEvent(Runnable task) {
            if (inline) task.run();
            else if (failure == null || enqueueBeforeFailure) super.queueEvent(task);
            if (failure != null) throwFailure(failure);
        }
    }
    private static void throwFailure(Throwable failure) {
        if (failure instanceof RuntimeException error) throw error;
        throw (Error)failure;
    }
    private static void failsWith(Throwable expected, Runnable action) {
        try { action.run(); throw new AssertionError("failure missing"); }
        catch (RuntimeException | Error actual) { check(actual == expected); }
    }
    private static void release(RenderContextBinding binding) {
        binding.beginRelease(); binding.drain(); binding.retire();
        check(binding.released());
    }
    private static void submissionFailures() {
        for (Throwable failure : new Throwable[]{new IllegalStateException("delegate failure"), new AssertionError("delegate error")}) {
            // Rejected work must not remove an accepted envelope carrying the
            // very same Runnable object, or reorder accepted siblings.
            SubmissionContext ordered = new SubmissionContext();
            RenderContextBinding binding = new RenderContextBinding(ordered, () -> {});
            List<String> calls = new ArrayList<>();
            Runnable same = () -> calls.add("same");
            binding.queueEvent(same);
            ordered.failure=failure;
            failsWith(failure, () -> binding.queueEvent(same));
            ordered.failure=null;
            binding.queueEvent(() -> calls.add("last"));
            release(binding);
            check(calls.equals(List.of("same", "last")));
            for (Runnable envelope : ordered.queued) envelope.run();
            check(calls.equals(List.of("same", "last")));

            SubmissionContext enqueued = new SubmissionContext();
            enqueued.failure=failure; enqueued.enqueueBeforeFailure=true;
            RenderContextBinding posted = new RenderContextBinding(enqueued, () -> {});
            int[] runs={0};
            failsWith(failure, () -> posted.queueEvent(() -> runs[0]++));
            check(enqueued.queued.size()==1);
            enqueued.queued.get(0).run(); // canceled while the binding is live
            check(runs[0]==0);
            release(posted);
            enqueued.queued.get(0).run(); // canceled after generation retirement
            check(runs[0]==0);

            // An inline callback owns its work before user code runs. Its own
            // exception and an exception after inline completion cannot replay it.
            for (boolean userFailure : new boolean[]{true, false}) {
                SubmissionContext inline = new SubmissionContext(); inline.inline=true;
                if (!userFailure) inline.failure=failure;
                RenderContextBinding running = new RenderContextBinding(inline, () -> {});
                int[] count={0};
                failsWith(failure, () -> running.queueEvent(() -> {
                    count[0]++;
                    if (userFailure) throwFailure(failure);
                }));
                check(count[0]==1);
                inline.failure=null;
                running.queueEvent(() -> count[0]++);
                release(running);
                check(count[0]==2);
            }

            // Activation may fail before Task.run claims the user's work.
            SubmissionContext activating = new SubmissionContext(); activating.inline=true;
            int[] activations={0}, activatedRuns={0};
            RenderContextBinding activation = new RenderContextBinding(activating, () -> {
                if (++activations[0]==1) throwFailure(failure);
            });
            failsWith(failure, () -> activation.queueEvent(() -> activatedRuns[0]++));
            check(activatedRuns[0]==0);
            activation.queueEvent(() -> activatedRuns[0]++);
            release(activation);
            check(activations[0]==2 && activatedRuns[0]==1);
        }
    }
    private static void overlappingSubmissionAndRelease() {
        java.util.concurrent.CountDownLatch entered=new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch reject=new java.util.concurrent.CountDownLatch(1);
        RuntimeException failure=new IllegalStateException("in-flight delegate rejection");
        Context context=new Context() {
            @Override public void queueEvent(Runnable task) {
                entered.countDown();
                try {
                    if (!reject.await(5, java.util.concurrent.TimeUnit.SECONDS))
                        throw new AssertionError("release did not complete");
                } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
                throw failure;
            }
        };
        RenderContextBinding binding=new RenderContextBinding(context, () -> {});
        int[] calls={0};
        java.util.concurrent.atomic.AtomicReference<Throwable> result=new java.util.concurrent.atomic.AtomicReference<>();
        Thread producer=new Thread(() -> {
            try { binding.queueEvent(() -> calls[0]++); }
            catch (Throwable error) { result.set(error); }
        });
        producer.start();
        try {
            check(entered.await(5, java.util.concurrent.TimeUnit.SECONDS));
            // Drain wins the claim before the delegate reports failure. That
            // already-started work cannot be undone or later replayed.
            release(binding);
        } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
        finally {
            reject.countDown();
            try { producer.join(5000); }
            catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
        }
        check(!producer.isAlive() && result.get()==failure && calls[0]==1);
        Context replacement=new Context();
        binding.rebind(replacement);
        binding.queueEvent(() -> calls[0]++);
        replacement.queued.get(0).run();
        check(calls[0]==2);
        release(binding);
        check(calls[0]==2);
    }
    /** A client-owned surface: it implements the legacy surface type, as the
     * AWT components of a desktop client do, and records its listeners. */
    static final class ClientSurface implements com.atakmap.map.RenderSurface {
        final List<OnSizeChangedListener> listeners = new ArrayList<>();
        public double getDpi() { return 96; }
        public int getWidth() { return 32; }
        public int getHeight() { return 24; }
        public void addOnSizeChangedListener(OnSizeChangedListener listener) { listeners.add(listener); }
        public void removeOnSizeChangedListener(OnSizeChangedListener listener) { listeners.remove(listener); }
    }
    static final class ClientContext extends Context {
        final RenderSurface client;
        ClientContext(RenderSurface client) { this.client=client; }
        @Override public RenderSurface getRenderSurface() { return client; }
    }
    private static void clientSurfaceIdentity() {
        ClientSurface component=new ClientSurface();
        RenderContextBinding binding=new RenderContextBinding(new ClientContext(component), () -> {});
        // Clients get their own surface object back, not a wrapper.
        check(binding.getRenderSurface() == component);
        // Only the native peer creation, on its own thread, captures the
        // forwarding surface; every other reader still sees the client's.
        Object[] concurrent={null};
        RenderSurface captured=binding.captureForNativePeer(() -> {
            Thread reader=new Thread(() -> concurrent[0]=binding.getRenderSurface());
            reader.start();
            try { reader.join(5000); } catch (InterruptedException error) { throw new AssertionError(error); }
            rejects(() -> binding.captureForNativePeer(() -> null));
            return binding.getRenderSurface();
        });
        check(captured != component && concurrent[0] == component);
        check(binding.getRenderSurface() == component);
        check(captured.getWidth() == 32 && captured.getHeight() == 24 && captured.getDpi() == 96);
        // Native size listeners stay counted and generation-guarded.
        int[] sizes={0};
        RenderSurface.OnSizeChangedListener listener=(surface, width, height) -> sizes[0]++;
        captured.addOnSizeChangedListener(listener);
        check(component.listeners.size() == 1);
        component.listeners.get(0).onSizeChanged(component, 16, 12);
        check(sizes[0] == 1);
        release(binding);
        component.listeners.get(0).onSizeChanged(component, 8, 6); // retired generation
        check(sizes[0] == 1);
        ClientContext replacement=new ClientContext(component);
        rejects(() -> binding.rebind(replacement)); // listener still registered
        captured.removeOnSizeChangedListener(listener);
        check(component.listeners.isEmpty());
        // The surface object is fixed at bind time. Another object is refused
        // before any state changes, even on the current GL thread.
        try { binding.rebind(new ClientContext(new ClientSurface())); throw new AssertionError("accepted another surface"); }
        catch (IllegalArgumentException expected) { assertions++; }
        check(binding.released() && !binding.isAttached());
        replacement.current=false;
        rejects(() -> binding.rebind(replacement)); // same surface, context not current
        replacement.current=true;
        binding.rebind(replacement);
        check(binding.getRenderSurface() == component && binding.isAttached() && binding.isRenderThread());
        release(binding);
        binding.terminate();
        // The terminal tombstone neither exposes nor retains the client's surface.
        check(binding.getRenderSurface() != component);
        rejects(() -> binding.captureForNativePeer(() -> null));
    }
    public static void main(String[] args) {
        Context old = new Context(), replacement = new Context(), other = new Context();
        RenderContextBinding binding = new RenderContextBinding(old, () -> {});
        RenderContextBinding live = new RenderContextBinding(other, () -> {});
        Object stableSurface = binding.getRenderSurface();
        int[] calls = {0};
        rejects(() -> binding.rebind(replacement));
        binding.queueEvent(() -> { calls[0]++; binding.queueEvent(() -> calls[0]++); });
        binding.beginRelease();
        java.util.concurrent.atomic.AtomicReference<Throwable> failure=new java.util.concurrent.atomic.AtomicReference<>();
        Thread external=new Thread(() -> {
            try { rejects(() -> binding.queueEvent(() -> calls[0]++)); }
            catch(Throwable error) { failure.set(error); }
        });
        external.start();
        try { external.join(3000); } catch(InterruptedException error) { throw new AssertionError(error); }
        check(!external.isAlive() && failure.get()==null);
        binding.drain();
        check(calls[0] == 2);
        binding.retire();
        check(!binding.isAttached() && !binding.isRenderThread());
        rejects(() -> binding.queueEvent(() -> calls[0]++));
        for (Runnable envelope : old.queued) envelope.run();
        check(calls[0] == 2);
        check(live.isAttached());
        binding.rebind(replacement);
        check(binding.getRenderSurface() == stableSurface);
        check(binding.isAttached());
        binding.queueEvent(() -> calls[0]++);
        replacement.queued.get(0).run();
        check(calls[0] == 3);
        replacement.current = false;
        rejects(binding::drain);
        check(live.isAttached());
        replacement.current = true;
        binding.beginRelease(); binding.drain(); binding.retire(); binding.terminate();
        rejects(() -> binding.rebind(new Context()));
        rejects(binding::requestRefresh);
        check(!binding.isAttached() && !binding.isRenderThread());
        for (int i=0;i<8;i++) {
            RenderContextBinding fresh = new RenderContextBinding(new Context(), () -> {});
            check(fresh != binding && fresh.isAttached());
            check(live.isAttached());
            rejects(() -> binding.queueEvent(() -> {}));
        }
        submissionFailures();
        overlappingSubmissionAndRelease();
        clientSurfaceIdentity();
        System.out.println("binding state assertions=" + assertions);
    }
}
