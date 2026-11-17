// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer;
import java.nio.file.*;
import java.util.*;
public final class LayerInboundControls {
 static native Layer create(boolean shared);
 static native long[] convert(Layer value,int loops);
 static native void pending(Layer value,Throwable primary);
 static native int alive();
 static int checks;
 static void check(boolean value,String reason){checks++;if(!value)throw new AssertionError(reason);}
 public static void main(String[] args)throws Exception {
  for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);System.load(args[1]);
  boolean shared=!args[2].startsWith("unique");Layer value=create(shared);check(value!=null&&alive()==1,"native wrapper absent");
  try {
   boolean disposed=args[2].endsWith("disposed");if(disposed){((gov.tak.api.util.Disposable)value).dispose();check(alive()==0,"native disposal did not retire layer");}
   if(args[2].equals("shared-pending")){
    RuntimeException primary=new RuntimeException("owned inbound primary");boolean caught=false;try{pending(value,primary);}catch(RuntimeException error){caught=true;check(error==primary,"pending primary identity changed");}check(caught,"pending primary disappeared");
   }else{
    long[] result=convert(value,args[2].endsWith("loop")?1024:1);
    if(disposed){check(result[0]==2,"disposed wrapper was not rejected with invalid argument");check(result[1]==1,"disposed conversion changed output ownership");}
    else{check(result[0]==0,"live wrapper conversion failed");check(result[2]==1,"live wrapper native identity changed");check(alive()==1,"live wrapper ownership lost");}
   }
  }finally{((gov.tak.api.util.Disposable)value).dispose();}
  check(alive()==0,"native owner leaked");Set<String> core=new HashSet<>(),jni=new HashSet<>();
  for(String line:Files.readAllLines(Path.of("/proc/self/maps"))){String path=line.substring(line.lastIndexOf(32)+1);if(path.endsWith("/libtakengine.so"))core.add(path);if(path.endsWith("/libtakenginejni.so"))jni.add(path);}
  check(core.equals(Set.of(args[3]))&&jni.equals(Set.of(args[4])),"mixed native producer mapping");
  System.out.println("LAYER_INBOUND_CONTROLS_PASS case="+args[2]+" checks="+checks);
 }
}
