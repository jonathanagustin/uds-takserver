// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer;
import com.atakmap.interop.Pointer;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
/** Sequential registration failure/recovery; no allocation or concurrency claim. */
public final class LayerRegistrationFault {
 static int checks;
 static void check(boolean value,String reason){checks++;if(!value)throw new AssertionError(reason);}
 static final class OwnedLayer implements Layer {
  boolean visible=true,armed=true;int removals;final String mode;
  final RuntimeException primary=new RuntimeException("owned registration primary");
  final RuntimeException secondary=new RuntimeException("owned removal secondary");
  final Set<OnLayerVisibleChangedListener> listeners=Collections.newSetFromMap(new IdentityHashMap<>());
  OwnedLayer(String mode){this.mode=mode;}
  public String getName(){return "owned registration failure layer";}
  public boolean isVisible(){return visible;}
  public void setVisible(boolean value){visible=value;for(var listener:new ArrayList<>(listeners))listener.onLayerVisibleChanged(this);}
  public void addOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){
   if(armed&&mode.equals("before"))throw primary;
   listeners.add(listener);
   if(armed)throw primary;
  }
  public void removeOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){
   removals++;listeners.remove(listener);
   if(armed&&mode.equals("secondary"))throw secondary;
  }
 }
 public static void main(String[] args)throws Exception {
  for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);
  OwnedLayer original=new OwnedLayer(args[1]);Pointer pointer=NativeLayer.wrap(original);
  NativeLayer peer=new NativeLayer(pointer,original);AtomicInteger events=new AtomicInteger();
  Layer.OnLayerVisibleChangedListener listener=layer->events.incrementAndGet();
  try {
   boolean caught=false;
   try {peer.addOnLayerVisibleChangedListener(listener);}catch(RuntimeException failure){caught=true;check(failure==original.primary,"primary exception identity lost");}
   check(caught,"registration did not fail");
   check(original.removals==1,"rollback removal not attempted exactly once");
   check(original.listeners.isEmpty(),"failed registration retained Java listener");
   check(peer.listeners.isEmpty(),"failed registration published native peer");
   original.armed=false;peer.addOnLayerVisibleChangedListener(listener);
   check(original.listeners.size()==1,"fresh registration failed");
   original.setVisible(false);check(events.get()==1,"fresh listener callback failed");
   peer.removeOnLayerVisibleChangedListener(listener);
   check(original.listeners.isEmpty()&&peer.listeners.isEmpty(),"fresh removal failed");
  }finally{original.armed=false;peer.dispose();}
  check(pointer.raw==0,"layer pointer not retired");
  System.out.println("LAYER_REGISTRATION_FAULT_PASS case="+args[1]+" checks="+checks);
 }
}
