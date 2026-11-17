// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer;
import java.net.*;
import java.nio.file.*;
import java.lang.reflect.*;
import java.util.*;

/** Loads unchanged real SDK classes; injects one dependency-loading failure. */
public final class LayerClassInitControls {
 static final String TARGET="com.atakmap.interop.NativePeerManager$CleanerThread";
 static final class Loader extends URLClassLoader {
  final RuntimeException primary=new RuntimeException("owned SDK class initialization failure");
  boolean armed;int blocked;
  Loader(URL[] urls){super(urls,ClassLoader.getPlatformClassLoader());}
  protected Class<?> loadClass(String name,boolean resolve)throws ClassNotFoundException {
   synchronized(getClassLoadingLock(name)){
    if(armed&&name.equals(TARGET)){blocked++;throw primary;}
    return super.loadClass(name,resolve);
   }
  }
 }
 public static void main(String[] args)throws Throwable {
  URL[] urls=new URL[args.length-3];for(int i=3;i<args.length;i++)urls[i-3]=Path.of(args[i]).toUri().toURL();
  try(Loader loader=new Loader(urls)){
   ClassLoader old=Thread.currentThread().getContextClassLoader();Thread.currentThread().setContextClassLoader(loader);
   try{
    Runnable arm=()->loader.armed=args[2].contains("-fault");Runnable disarm=()->loader.armed=false;
    Class<?> scenario=Class.forName("com.atakmap.map.layer.LayerClassInitControls$Scenario",true,loader);
    try{scenario.getMethod("run",String[].class,RuntimeException.class,Runnable.class,Runnable.class).invoke(null,new String[]{args[0],args[1],args[2]},loader.primary,arm,disarm);}
    catch(InvocationTargetException error){throw error.getCause();}
    if(args[2].contains("-fault")&&loader.blocked!=1)throw new AssertionError("fault target did not initialize exactly once");
   }finally{Thread.currentThread().setContextClassLoader(old);}
  }
 }
 public static final class Scenario {
  static int checks;
  static void check(boolean value,String reason){checks++;if(!value)throw new AssertionError(reason);}
  static native Layer createShared();static native Layer createUnique();static native long[] counts();
  static native void holdNative();static native void releaseHeld();static native Layer reference();static native Layer wrapHeld();static native void setCleanupError(Throwable value);
  static Layer create(boolean shared){return shared?createShared():createUnique();}
  public static void run(String[] args,RuntimeException primary,Runnable arm,Runnable disarm)throws Exception {
   for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);System.load(args[1]);
   boolean shared=args[2].startsWith("shared");arm.run();
   boolean secondary=args[2].endsWith("-secondary");RuntimeException cleanup=new RuntimeException("owned cleanup secondary");
   if(secondary)setCleanupError(cleanup);
   if(args[2].contains("-fault")){
    boolean caught=false;
    try{create(shared);}catch(ExceptionInInitializerError failure){caught=true;check(failure.getCause()==primary,"class initialization primary identity lost");}
    check(caught,"class initialization did not fail");
    long[] state=counts();check(state[0]==0,"failed wrapper retained native layer");check(state[1]==1&&state[2]==1,"failed wrapper cleanup count differs");check(state[3]==0&&state[4]!=0,"failed wrapper was published or reported successful");
    if(secondary)check(state[5]==1,"secondary cleanup not attempted");
    disarm.run();if(secondary)setCleanupError(cleanup);caught=false;try{create(shared);}catch(NoClassDefFoundError failure){caught=true;}
    check(caught,"poisoned initializer unexpectedly recovered");state=counts();check(state[0]==0&&state[1]==2&&state[2]==2,"poisoned wrapper retained native layer");check(state[3]==0&&state[4]!=0,"poisoned wrapper was published or reported successful");if(secondary)check(state[5]==2,"poisoned secondary cleanup not attempted");
   }else{
    Layer value=create(shared);check(value!=null,"normal wrapper missing");check(value.getClass().getClassLoader()==Scenario.class.getClassLoader(),"SDK loader mismatch");
    try{
     check(value.getName().equals("owned class-init layer"),"native wrapper name differs");check(counts()[0]==1,"normal native layer absent");
     java.util.concurrent.atomic.AtomicInteger events=new java.util.concurrent.atomic.AtomicInteger();Layer.OnLayerVisibleChangedListener listener=subject->{check(subject==value,"native callback subject differs");events.incrementAndGet();};
     value.addOnLayerVisibleChangedListener(listener);value.setVisible(false);check(events.get()==1,"native visibility callback missing");value.removeOnLayerVisibleChangedListener(listener);check(reference()==value,"normal const reference lookup differs");
    }finally{((gov.tak.api.util.Disposable)value).dispose();}
    check(counts()[0]==0,"normal wrapper did not dispose");
    if(args[2].equals("shared-recycle")){
     Layer next=create(true);try{check(next!=value,"recycled native address reused disposed Java peer");check(next.getName().equals("owned class-init layer"),"recycled wrapper unusable");check(counts()[0]==1,"recycled layer absent");}finally{((gov.tak.api.util.Disposable)next).dispose();}check(counts()[0]==0,"recycled wrapper did not dispose");
    }
    if(args[2].equals("shared-reference")){
     holdNative();try{
      check(reference()==null,"recycled const reference reused disposed Java peer");Layer next=wrapHeld();check(next!=value,"held wrapper reused retired peer");check(reference()==next,"held const reference lookup differs");((gov.tak.api.util.Disposable)next).dispose();check(reference()==null,"const reference exposed disposed held peer");check(counts()[0]==1,"native owner lost its layer");
     }finally{releaseHeld();}check(counts()[0]==0,"held native owner did not release");
    }
   }
   System.out.println("LAYER_CLASS_INIT_CONTROLS_PASS case="+args[2]+" checks="+checks);
  }
 }
}
