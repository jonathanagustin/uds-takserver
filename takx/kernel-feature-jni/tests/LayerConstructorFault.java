// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer;
import com.atakmap.interop.Pointer;
import java.nio.file.*;
public final class LayerConstructorFault {
  static final RuntimeException primary=new RuntimeException("owned getName constructor fault");
  static final class OwnedLayer implements Layer {
    boolean fail; int names;
    OwnedLayer(boolean fail){this.fail=fail;}
    public String getName(){names++;if(fail)throw primary;return "owned constructor recovery";}
    public boolean isVisible(){return true;}
    public void setVisible(boolean value){}
    public void addOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){}
    public void removeOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){}
  }
  static void positive(){
    OwnedLayer layer=new OwnedLayer(false);Pointer pointer=NativeLayer.wrap(layer);
    if(pointer==null||pointer.raw==0||layer.names!=1)throw new AssertionError("fresh constructor recovery failed");
    NativeLayer.destruct(pointer);
  }
  public static void main(String[] args)throws Exception {
    for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);
    positive();
    OwnedLayer layer=new OwnedLayer(true);
    try{NativeLayer.wrap(layer);throw new AssertionError("missing primary exception");}
    catch(RuntimeException error){if(error!=primary)throw new AssertionError("primary exception replaced",error);}
    if(layer.names!=1)throw new AssertionError("fault callback not exercised");
    System.out.println("PRIMARY_EXCEPTION_IDENTITY_PRESERVED");
    positive();System.out.println("FRESH_CONSTRUCTOR_RECOVERY_COMPLETED");
  }
}
