// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer;
import com.atakmap.interop.Pointer;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
/** Sequential unknown/repeated removal, including a failed registration. */
public final class LayerRemovalControls {
 static int checks;
 static void check(boolean value,String reason){checks++;if(!value)throw new AssertionError(reason);}
 static final class OwnedLayer implements Layer {
  boolean visible=true,failAdd;int removals;
  final RuntimeException primary=new RuntimeException("owned registration primary");
  final Set<OnLayerVisibleChangedListener> listeners=Collections.newSetFromMap(new IdentityHashMap<>());
  public String getName(){return "owned listener removal layer";}
  public boolean isVisible(){return visible;}
  public void setVisible(boolean value){visible=value;for(var listener:new ArrayList<>(listeners))listener.onLayerVisibleChanged(this);}
  public void addOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){if(failAdd)throw primary;listeners.add(listener);}
  public void removeOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){removals++;listeners.remove(listener);}
 }
 public static void main(String[] args)throws Exception {
  for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);
  OwnedLayer original=new OwnedLayer();Pointer pointer=NativeLayer.wrap(original);
  NativeLayer peer=new NativeLayer(pointer,original);AtomicInteger events=new AtomicInteger();
  Layer.OnLayerVisibleChangedListener listener=layer->events.incrementAndGet();
  try {
   if(args[1].equals("double")){peer.addOnLayerVisibleChangedListener(listener);peer.removeOnLayerVisibleChangedListener(listener);}
   if(args[1].equals("failed")){
    original.failAdd=true;boolean caught=false;
    try{peer.addOnLayerVisibleChangedListener(listener);}catch(RuntimeException failure){caught=true;check(failure==original.primary,"primary identity lost");}
    check(caught,"registration did not fail");original.failAdd=false;
   }
   int previous=original.removals;
   peer.removeOnLayerVisibleChangedListener(null);
   peer.removeOnLayerVisibleChangedListener(listener);
   check(original.removals==previous,"unknown removal called Java");
   check(original.listeners.isEmpty()&&peer.listeners.isEmpty(),"unknown removal changed listener state");
   peer.addOnLayerVisibleChangedListener(listener);check(original.listeners.size()==1,"fresh registration failed");
   original.setVisible(false);check(events.get()==1,"fresh callback failed");
   peer.removeOnLayerVisibleChangedListener(listener);check(original.listeners.isEmpty()&&peer.listeners.isEmpty(),"fresh removal failed");
   peer.removeOnLayerVisibleChangedListener(listener);check(original.removals==previous+1,"repeated removal called Java");
  }finally{original.failAdd=false;peer.dispose();}
  check(pointer.raw==0,"layer pointer not retired");check(original.listeners.isEmpty(),"retired layer retained listener");
  System.out.println("LAYER_REMOVAL_CONTROLS_PASS case="+args[1]+" checks="+checks);
 }
}
