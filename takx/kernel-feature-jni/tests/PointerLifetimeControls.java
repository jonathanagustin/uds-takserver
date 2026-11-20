// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer;
import com.atakmap.interop.Pointer;
import java.nio.file.*;
import java.util.*;
public final class PointerLifetimeControls {
 static native Layer create(boolean shared);
 static native long[] inspect(Pointer pointer,int action,Throwable primary);
 static native int alive();
 static int checks;
 static void check(boolean value,String reason){checks++;if(!value)throw new AssertionError(reason);}
 public static void main(String[] args)throws Exception {
  for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);System.load(args[1]);
  String mode=args[2];boolean unique=mode.contains("unique");Layer value=create(!unique);check(value instanceof NativeLayer&&alive()==1,"native wrapper absent");
  try {
   Pointer pointer=((NativeLayer)value).pointer;if(mode.startsWith("retired")){((gov.tak.api.util.Disposable)value).dispose();check(alive()==0,"native layer did not retire");}
   if(mode.startsWith("null"))pointer=null;
   int action=mode.equals("alloc-unique-model")?4:mode.equals("alloc-unique-control")?5:mode.endsWith("raw")?0:mode.endsWith("get")?1:mode.endsWith("promote")?2:3;
   long raw=pointer==null?0:pointer.raw,holder=pointer==null?0:pointer.value;int type=pointer==null?-1:pointer.type;
   if(mode.startsWith("pending")){
    RuntimeException primary=new RuntimeException("owned Pointer helper primary");boolean caught=false;
    try{inspect(pointer,action,primary);}catch(RuntimeException error){caught=true;check(error==primary,"Pointer helper replaced primary");}
    check(caught,"Pointer helper swallowed primary");check(pointer.raw==raw&&pointer.value==holder&&pointer.type==type,"pending helper mutated pointer fields");
   }else{
    long[] result=inspect(pointer,action,null);check(result!=null,"helper result missing");
    if(mode.startsWith("alloc")){
     check(result[0]==0&&alive()==1,"allocation refusal retired the Java-owned object");
     check(pointer.raw==raw&&pointer.value==holder&&pointer.type==type,"allocation refusal mutated pointer fields");
     check(inspect(pointer,2,null)[0]==1&&pointer.type==Pointer.SHARED,"allocation recovery failed");
     check(inspect(pointer,1,null)[2]==1,"allocation recovery changed native identity");
    }else if(mode.startsWith("live")){
     check(result[0]==(action==1?0:1),"live helper failed");if(action==1)check(result[2]==1,"live native identity changed");
     if(action==2){check(pointer.type==Pointer.SHARED,"unique pointer did not promote");check(inspect(pointer,1,null)[2]==1,"promoted identity changed");}
     check(alive()==1,"live native ownership lost");
    }else{
     check(result[0]==(action==1?2:0),"retired or null pointer helper accepted input");if(action==1)check(result[1]==1,"rejected helper changed output ownership");
     if(pointer!=null)check(pointer.raw==raw&&pointer.value==holder&&pointer.type==type,"rejected helper mutated pointer fields");
    }
   }
  }finally{((gov.tak.api.util.Disposable)value).dispose();}
  check(alive()==0,"native owner leaked");Set<String> core=new HashSet<>(),jni=new HashSet<>();
  for(String line:Files.readAllLines(Path.of("/proc/self/maps"))){String path=line.substring(line.lastIndexOf(32)+1);if(path.endsWith("/libtakengine.so"))core.add(path);if(path.endsWith("/libtakenginejni.so"))jni.add(path);}
  check(core.equals(Set.of(args[3]))&&jni.equals(Set.of(args[4])),"mixed native producer mapping");
  System.out.println("POINTER_LIFETIME_CONTROLS_PASS case="+mode+" checks="+checks);
 }
}
