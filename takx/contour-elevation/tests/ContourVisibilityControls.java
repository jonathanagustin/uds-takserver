// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
import gov.tak.project.contour.ContourLayer;
import com.atakmap.map.MapRenderer;
import com.atakmap.map.MapRenderer3;
import com.atakmap.map.layer.control.SurfaceRendererControl;
import java.lang.reflect.Proxy;
import java.util.ArrayDeque;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Real SDK interfaces; controlled draw queue and surface for callback retirement. */
public final class ContourVisibilityControls {
    static int checks;
    static void check(boolean value,String why){if(!value)throw new AssertionError(why);checks++;}
    static final class Renderer {
        final ArrayDeque<Runnable> queue=new ArrayDeque<>();
        final AtomicInteger dirty=new AtomicInteger();
        final CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
        volatile Thread actor;
        volatile boolean block;
        final MapRenderer renderer;
        Renderer(boolean modern,boolean hasSurface){
            SurfaceRendererControl surface=(SurfaceRendererControl)Proxy.newProxyInstance(
                SurfaceRendererControl.class.getClassLoader(),new Class<?>[]{SurfaceRendererControl.class},
                (proxy,method,args)->{
                    if(method.getName().equals("markDirty")&&(args==null||args.length==0)){
                        if(Thread.currentThread()!=actor)throw new AssertionError("off-draw invalidation");
                        entered.countDown();
                        if(block&&!release.await(3,TimeUnit.SECONDS))throw new AssertionError("invalidation release timeout");
                        dirty.incrementAndGet();return null;
                    }
                    throw new AssertionError("unexpected surface operation "+method.getName());
                });
            renderer=(MapRenderer)Proxy.newProxyInstance(MapRenderer.class.getClassLoader(),
                modern?new Class<?>[]{MapRenderer.class,MapRenderer3.class}:new Class<?>[]{MapRenderer.class},
                (proxy,method,args)->{
                    switch(method.getName()){
                        case "getControl":return hasSurface&&args[0]==SurfaceRendererControl.class?surface:null;
                        case "isRenderThread":return Thread.currentThread()==actor;
                        case "queueEvent":queue.add((Runnable)args[0]);return null;
                        default:throw new AssertionError("unexpected renderer operation "+method.getName());
                    }
                });
        }
        void drain(){actor=Thread.currentThread();try{while(!queue.isEmpty())queue.remove().run();}finally{actor=null;}}
    }
    static void rejects(Runnable action){try{action.run();throw new AssertionError("missing binding refusal");}catch(IllegalArgumentException expected){checks++;}}
    public static int run(ContourLayer owner)throws Exception{
        var layer=owner.layer();
        rejects(()->owner.bindRenderer(new Renderer(false,true).renderer));
        rejects(()->owner.bindRenderer(new Renderer(true,false).renderer));
        Renderer r=new Renderer(true,true);var binding=owner.bindRenderer(r.renderer);
        layer.setVisible(false);check(r.queue.size()==1&&r.dirty.get()==0,"off-thread update was not queued");
        r.drain();check(r.dirty.get()==1,"queued update did not invalidate");
        r.actor=Thread.currentThread();try{layer.setVisible(true);}finally{r.actor=null;}
        check(r.queue.isEmpty()&&r.dirty.get()==2,"draw-thread update was not inline");
        layer.setVisible(false);check(r.queue.size()==1,"cancellation case was not queued");
        binding.close();binding.close();r.drain();check(r.dirty.get()==2,"closed binding ran queued work");
        layer.setVisible(true);check(r.queue.isEmpty(),"closed listener was retained");

        Renderer flight=new Renderer(true,true);var inFlight=owner.bindRenderer(flight.renderer);
        layer.setVisible(false);check(flight.queue.size()==1,"in-flight case was not queued");
        flight.block=true;AtomicReference<Throwable> failure=new AtomicReference<>();
        CountDownLatch closing=new CountDownLatch(1),closed=new CountDownLatch(1);
        Thread draw=new Thread(()->{try{flight.drain();}catch(Throwable error){failure.set(error);}},"owned-visibility-draw");
        Thread closer=new Thread(()->{closing.countDown();try{inFlight.close();}catch(Throwable error){failure.set(error);}finally{closed.countDown();}},"owned-visibility-close");
        draw.start();
        try{
            check(flight.entered.await(3,TimeUnit.SECONDS),"invalidation did not enter");
            closer.start();check(closing.await(3,TimeUnit.SECONDS),"close did not start");
            check(!closed.await(100,TimeUnit.MILLISECONDS),"close returned during another thread's invalidation");
        }finally{flight.release.countDown();draw.join(3000);if(closer.getState()!=Thread.State.NEW)closer.join(3000);}
        check(!draw.isAlive()&&!closer.isAlive()&&failure.get()==null,"callback retirement failed");
        check(flight.dirty.get()==1,"in-flight invalidation was lost");
        layer.setVisible(true);check(flight.queue.isEmpty(),"retired in-flight listener was retained");

        Renderer owned=new Renderer(true,true);var auto=owner.bindRenderer(owned.renderer);
        layer.setVisible(false);check(owned.queue.size()==1,"owner-retirement case was not queued");
        owner.close();owned.drain();auto.close();check(owned.dirty.get()==0,"owner close retained queued work");
        try{owner.bindRenderer(owned.renderer);throw new AssertionError("closed owner accepted binding");}
        catch(IllegalStateException expected){checks++;}
        System.out.println("CONTOUR_VISIBILITY_CONTROLS_PASS checks="+checks);
        return checks;
    }
}
