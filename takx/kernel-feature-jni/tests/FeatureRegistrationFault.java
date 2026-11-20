// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer.feature;
import com.atakmap.interop.Pointer;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
public final class FeatureRegistrationFault {
  private static native void destructWithPending(Pointer pointer,RuntimeException primary);
  static final RuntimeException primary=new RuntimeException("owned listener registration fault");
  static final RuntimeException cleanup=new RuntimeException("owned secondary removal fault");
  static final class Owned implements InvocationHandler {
    final List<Object> listeners=new ArrayList<>();
    final boolean fail,secondary;
    Owned(boolean fail){this(fail,false);}
    Owned(boolean fail,boolean secondary){this.fail=fail;this.secondary=secondary;}
    public Object invoke(Object proxy,Method method,Object[] args){
      switch(method.getName()) {
        case "addOnDataStoreContentChangedListener":listeners.add(args[0]);if(fail)throw primary;return null;
        case "removeOnDataStoreContentChangedListener":if(!listeners.remove(args[0]))throw new AssertionError("removal identity differs");if(secondary)throw cleanup;return null;
        case "hashCode":return System.identityHashCode(proxy);
        case "equals":return proxy==args[0];
        case "toString":return "owned listener fault store";
        default:throw new AssertionError("unexpected callback "+method.getName());
      }
    }
    FeatureDataStore4 store(){return (FeatureDataStore4)Proxy.newProxyInstance(FeatureRegistrationFault.class.getClassLoader(),new Class<?>[]{FeatureDataStore4.class},this);}
  }
  static void positive(){
    Owned owned=new Owned(false);Pointer pointer=NativeFeatureDataStore4.wrap(owned.store());
    if(pointer==null||pointer.raw==0||owned.listeners.size()!=1)throw new AssertionError("fresh registration failed");
    NativeFeatureDataStore4.destruct(pointer);
    if(!owned.listeners.isEmpty())throw new AssertionError("successful wrapper cleanup failed");
  }
  public static void main(String[] args)throws Exception {
    for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);
    System.load(args[1]);
    positive();Owned owned=new Owned(true);
    try{NativeFeatureDataStore4.wrap(owned.store());throw new AssertionError("missing primary exception");}
    catch(RuntimeException error){if(error!=primary)throw new AssertionError("primary exception replaced",error);}
    System.out.println("PRIMARY_EXCEPTION_IDENTITY_PRESERVED");
    positive();System.out.println("FRESH_CONSTRUCTOR_RECOVERY_COMPLETED");
    System.out.println("FAILED_CONSTRUCTOR_RETAINED_LISTENERS "+owned.listeners.size());
    if(!owned.listeners.isEmpty())throw new AssertionError("failed constructor retained registered callback");
    Owned secondary=new Owned(true,true);
    try{NativeFeatureDataStore4.wrap(secondary.store());throw new AssertionError("missing primary with secondary cleanup");}
    catch(RuntimeException error){if(error!=primary)throw new AssertionError("cleanup replaced primary",error);}
    if(!secondary.listeners.isEmpty())throw new AssertionError("secondary cleanup retained callback");
    positive();System.out.println("SECONDARY_CLEANUP_PRIMARY_PRESERVED_AND_RECOVERED");
    Owned cleanupOnly=new Owned(false,true);Pointer pointer=NativeFeatureDataStore4.wrap(cleanupOnly.store());
    try{NativeFeatureDataStore4.destruct(pointer);throw new AssertionError("missing disposal failure");}
    catch(RuntimeException error){if(error!=cleanup)throw new AssertionError("disposal exception replaced",error);}
    if(pointer.raw!=0||!cleanupOnly.listeners.isEmpty())throw new AssertionError("disposal left a live pointer or registration");
    positive();System.out.println("DISPOSAL_EXCEPTION_IDENTITY_POINTER_CLEAR_AND_RECOVERY_COMPLETED");
    Owned pending=new Owned(false,true);Pointer pendingPointer=NativeFeatureDataStore4.wrap(pending.store());
    try{destructWithPending(pendingPointer,primary);throw new AssertionError("missing entry pending exception");}
    catch(RuntimeException error){if(error!=primary)throw new AssertionError("secondary disposal replaced pending primary",error);}
    if(pendingPointer.raw!=0||!pending.listeners.isEmpty())throw new AssertionError("pending disposal left pointer or callback");
    positive();System.out.println("PENDING_ENTRY_PRIMARY_POINTER_CLEAR_AND_RECOVERY_COMPLETED");
    String expected=Files.readAllLines(Path.of(args[0])).stream().map(line->line.split(" ",2)[1]).filter(line->line.endsWith("/libtakenginejni.so")).findFirst().orElseThrow();
    Set<String> mapped=new HashSet<>();
    for(String line:Files.readAllLines(Path.of("/proc/self/maps")))if(line.endsWith("/libtakenginejni.so"))mapped.add(line.substring(line.lastIndexOf(' ')+1));
    if(!mapped.equals(Set.of(expected)))throw new AssertionError("JNI provider mapping differs");
    System.out.println("ONLY_EXPECTED_JNI_LIBRARY_MAPPED "+expected);



  }
}
