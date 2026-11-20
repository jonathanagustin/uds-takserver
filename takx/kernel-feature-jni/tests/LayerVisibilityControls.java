// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer;
import com.atakmap.interop.Pointer;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
/** Sequential cached-wrapper/listener control; no concurrent cleanup claim. */
public final class LayerVisibilityControls {
 static int checks;
 static void check(boolean value){checks++;if(!value)throw new AssertionError("visibility/cache control "+checks);}
 static final class OwnedLayer implements Layer {
  boolean visible=true;int names;
  final Set<OnLayerVisibleChangedListener> listeners=Collections.newSetFromMap(new IdentityHashMap<>());
  public String getName(){names++;return "owned cached layer";}
  public boolean isVisible(){return visible;}
  public void setVisible(boolean value){visible=value;for(var listener:new ArrayList<>(listeners))listener.onLayerVisibleChanged(this);}
  public void addOnLayerVisibleChangedListener(OnLayerVisibleChangedListener value){listeners.add(value);}
  public void removeOnLayerVisibleChangedListener(OnLayerVisibleChangedListener value){listeners.remove(value);}
 }
 public static void main(String[] args)throws Exception {
  for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);
  OwnedLayer original=new OwnedLayer();Pointer first=NativeLayer.wrap(original),second=null;
  NativeLayer peer=new NativeLayer(first,original);AtomicInteger events=new AtomicInteger();
  Layer.OnLayerVisibleChangedListener listener=layer->events.incrementAndGet();
  try {
   second=NativeLayer.wrap(original);check(second!=null&&second.raw==first.raw);check(original.names==1);
   peer.addOnLayerVisibleChangedListener(listener);check(original.listeners.size()==1);
   original.setVisible(false);check(!peer.isVisible());check(events.get()==1);
   original.setVisible(true);check(peer.isVisible());check(events.get()==2);
   peer.setVisible(false);check(!original.isVisible());check(events.get()==3);
   peer.setVisible(true);check(original.isVisible());check(events.get()==4);
   peer.removeOnLayerVisibleChangedListener(listener);check(original.listeners.isEmpty());
   original.setVisible(false);check(events.get()==4);
  } finally {peer.dispose();if(second!=null)NativeLayer.destruct(second);}
  check(first.raw==0);check(second.raw==0);check(original.listeners.isEmpty());
  System.out.println("LAYER_VISIBILITY_CACHE_CONTROLS_PASS checks="+checks);
 }
}
