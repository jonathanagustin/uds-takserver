// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.opengl;
import com.atakmap.map.*;
import com.atakmap.map.layer.feature.opengl.GLBatchGeometryFeatureDataStoreRenderer;
import com.atakmap.map.layer.opengl.GLLayerFactory;
import com.atakmap.map.elevation.*;
import com.atakmap.map.layer.feature.geometry.*;
import com.jogamp.opengl.*;
import com.jogamp.opengl.awt.GLCanvas;
import com.jogamp.opengl.util.FPSAnimator;
import gov.tak.platform.engine.JOGLRenderContext;
import gov.tak.platform.commons.opengl.GLES30;
import gov.tak.project.contour.*;
import java.awt.Dimension;
import java.awt.EventQueue;
import java.awt.Frame;
import java.awt.image.BufferedImage;
import java.nio.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.imageio.ImageIO;

public final class ContourDrawProbe implements GLEventListener {
    static final AtomicReference<Throwable> failure=new AtomicReference<>();
    static void check(boolean v,String why){if(!v)throw new AssertionError(why);}
    final GLCanvas canvas;final ContourLayer owner;final Path output;
    final Globe globe=new Globe();final JOGLRenderContext context;
    GLMapView view;int frames;boolean capturedImage;volatile boolean released;
    record Pixels(int sequence,int major,int minor){}
    volatile Pixels pixels=new Pixels(0,0,0);
    ContourDrawProbe(GLCanvas canvas,ContourLayer owner,Path output){
        this.canvas=canvas;this.owner=owner;this.output=output;
        context=new JOGLRenderContext(canvas);
        view=new GLMapView(new RenderContextBinding(context),globe,0,0,320,240);
        owner.layer().getOptions().labelsEnabled=false;
        owner.layer().setVisible(false);globe.addLayer(owner.layer());
        view.setBaseMap(new GLMapRenderable(){
            public void draw(GLMapView ignored){GLES30.glClearColor(0,0,0,1);GLES30.glClear(GLES30.GL_COLOR_BUFFER_BIT|GLES30.GL_DEPTH_BUFFER_BIT);}
            public void release(){}
        });
    }
    public void init(GLAutoDrawable drawable){try{
        HeadedAdmission.verifyMaps("contour-init",true);
        String renderer=drawable.getGL().glGetString(GL.GL_RENDERER);
        check(renderer.toLowerCase(Locale.ROOT).contains("llvmpipe"),"owned software renderer required");
        System.out.println("CONTOUR_GL_PROVIDER "+renderer);
        view.start();check(view.lookAt(new com.atakmap.coremap.maps.coords.GeoPoint(0,0),500,0,0,false),"lookAt refused");
    }catch(Throwable error){failure.compareAndSet(null,error);}}
    public void display(GLAutoDrawable drawable){try{
        if(released)return;
        GLAnimatorControl animator=canvas.getAnimator();
        if(animator!=null&&animator.isAnimating())check(Thread.currentThread()==animator.getThread(),"animator draw owner required");
        // Match the real TAKX JOGLMapRendererSpi host clear before rendering.
        drawable.getGL().glClear(GL.GL_COLOR_BUFFER_BIT|GL.GL_DEPTH_BUFFER_BIT|GL.GL_STENCIL_BUFFER_BIT);
        view.render();
        int[] framebuffer={-1};GLES30.glGetIntegerv(GL.GL_READ_FRAMEBUFFER_BINDING,framebuffer,0);
        check(framebuffer[0]==drawable.getContext().getDefaultReadFramebuffer(),"renderer did not restore read framebuffer");
        ByteBuffer pixels=ByteBuffer.allocateDirect(drawable.getSurfaceWidth()*drawable.getSurfaceHeight()*4);
        GLES30.glReadPixels(0,0,drawable.getSurfaceWidth(),drawable.getSurfaceHeight(),GLES30.GL_RGBA,GLES30.GL_UNSIGNED_BYTE,pixels);
        check(GLES30.glGetError()==GLES30.GL_NO_ERROR,"GL error after actual contour-layer draw/readback");
        int major=0,minor=0;
        for(int i=0;i<pixels.capacity();i+=4){int r=pixels.get(i)&255,g=pixels.get(i+1)&255,b=pixels.get(i+2)&255;
            if(r<25&&g>230&&b<25)major++;
            if(r>230&&g<25&&b>230)minor++;
        }
        frames++;
        if(!capturedImage&&owner.layer().isVisible()&&major>20&&minor>20){
            BufferedImage image=new BufferedImage(drawable.getSurfaceWidth(),drawable.getSurfaceHeight(),BufferedImage.TYPE_INT_ARGB);
            for(int y=0;y<image.getHeight();y++)for(int x=0;x<image.getWidth();x++){
                int i=(y*image.getWidth()+x)*4;
                image.setRGB(x,image.getHeight()-1-y,0xff000000|((pixels.get(i)&255)<<16)|((pixels.get(i+1)&255)<<8)|(pixels.get(i+2)&255));
            }
            ImageIO.write(image,"png",output.resolve("actual-contours.png").toFile());
            capturedImage=true;
        }
        this.pixels=new Pixels(frames,major,minor);
    }catch(Throwable error){failure.compareAndSet(null,error);}}
    public void reshape(GLAutoDrawable d,int x,int y,int w,int h){}
    public void dispose(GLAutoDrawable drawable){disposeNow();}
    void disposeNow(){if(released)return;released=true;try{
        var binding=visibilityBinding;visibilityBinding=null;if(binding!=null)binding.close();
        globe.removeLayer(owner.layer());
        ((RenderContextBinding)view.getRenderContext()).beginRelease();
        view.stop();view.release();view.dispose();
    }catch(Throwable error){failure.compareAndSet(null,error);}}
    volatile ContourLayer.VisibilityBinding visibilityBinding;
    static void healthy(){if(failure.get()!=null)throw new AssertionError("actual contour draw failed",failure.get());}
    static void pump(ContourDrawProbe probe,int limit,boolean visible)throws Exception{
        int floor=probe.pixels.sequence;long deadline=System.nanoTime()+10_000_000_000L;
        while(System.nanoTime()<deadline){
            healthy();Pixels sample=probe.pixels;
            if(sample.sequence>floor&&(visible?sample.major>20&&sample.minor>20:sample.major==0&&sample.minor==0))return;
            if(sample.sequence-floor>=limit)break;
            Thread.sleep(20);
        }
        Pixels sample=probe.pixels;
        throw new AssertionError("actual layer pixel condition absent visible="+visible+" major="+sample.major+" minor="+sample.minor);
    }
    public static void main(String[] args)throws Exception{
        Thread.setDefaultUncaughtExceptionHandler((thread,error)->{failure.compareAndSet(null,error);error.printStackTrace(System.err);});
        HeadedAdmission.checkOrigins();HeadedAdmission.verifyMaps("before-contour-load",false);
        for(String line:Files.readAllLines(Path.of(args[0])))if(!line.isBlank())System.load(line);
        System.load(args[1]);EngineLibrary.initialize();Threading.disableSingleThreading();
        Path output=Path.of(args[2]);
        Polygon bounds=new Polygon(2);LineString ring=new LineString(2);
        ring.addPoint(-1,-1);ring.addPoint(1,-1);ring.addPoint(1,1);ring.addPoint(-1,1);ring.addPoint(-1,-1);bounds.addRing(ring);
        ElevationChunk chunk=ElevationChunk.Factory.create("owned-draw-plane","memory://owned-draw-plane",ElevationData.MODEL_TERRAIN,1,bounds,0,0,true,new ElevationChunk.Factory.Sampler(){
            public double sample(double lat,double lon){return 100*lon+50*lat;}public void dispose(){}
        });
        ElevationSource source=ElevationSourceBuilder.build("owned-draw-plane",java.util.List.of(chunk));
        ElevationSourceManager.attach(source);ElevationContours.Result contours;
        try{contours=ElevationContours.generate("owned-draw-plane",ElevationData.MODEL_TERRAIN,9,9,-.8,-.8,.8,.8,25,0,null,null).clipped(-.3,-.3,.3,.3,null,null);}
        finally{ElevationSourceManager.detach(source);chunk.dispose();bounds.dispose();ring.dispose();}
        GLLayerFactory.register(GLBatchGeometryFeatureDataStoreRenderer.SPI);
        Frame[] window={null};ContourDrawProbe[] probe={null};FPSAnimator[] animator={null};
        try(var batch=ContourFeatures.create(contours.lines(),25,0,2,0xffff00ff,0xff00ff00,3,6,null);
            var layer=ContourLayer.create("Owned pixel contours",output.resolve("owned-contours.sqlite"),batch)){
            try{
                EventQueue.invokeAndWait(()->{
                    GLCanvas canvas=new GLCanvas(new GLCapabilities(GLProfile.getMaxProgrammable(true)));
                    canvas.setPreferredSize(new Dimension(320,240));probe[0]=new ContourDrawProbe(canvas,layer,output);canvas.addGLEventListener(probe[0]);
                    animator[0]=new FPSAnimator(canvas,30,true);
                    window[0]=new Frame("Owned actual contour rendering");window[0].add(canvas);window[0].pack();window[0].setVisible(true);
                });
                probe[0].visibilityBinding=layer.bindRenderer(probe[0].view);
                animator[0].start();
                pump(probe[0],30,false);System.out.println("CONTOUR_HIDDEN_BASELINE major=0 minor=0");
                for(int cycle=0;cycle<3;cycle++){
                EventQueue.invokeAndWait(()->layer.layer().setVisible(true));pump(probe[0],120,true);
                Pixels visible=probe[0].pixels;System.out.println("CONTOUR_VISIBLE_PIXELS major="+visible.major+" minor="+visible.minor);
                EventQueue.invokeAndWait(()->layer.layer().setVisible(false));pump(probe[0],120,false);
                System.out.println("CONTOUR_HIDDEN_AFTER major=0 minor=0");
                    System.out.println("CONTOUR_BINDING_CYCLE_PASS "+cycle);
                }
                HeadedAdmission.verifyMaps("after-contour-draw",true);
            }finally{
                if(window[0]!=null){
                    try{probe[0].canvas.invoke(true,drawable->{probe[0].disposeNow();return true;});}
                    finally{
                        try{if(animator[0]!=null)animator[0].stop();}
                        finally{EventQueue.invokeAndWait(()->{probe[0].canvas.destroy();window[0].dispose();});}
                    }
                }
            }
            healthy();
        }finally{GLLayerFactory.unregister(GLBatchGeometryFeatureDataStoreRenderer.SPI);}
        System.out.println("CONTOUR_DRAW_CONTROLS_PASS actual-layer/visibility/pixels animator-owned software-only");
    }
}
