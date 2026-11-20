// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package com.atakmap.map.layer.opengl;
import com.atakmap.map.layer.Layer;
import com.atakmap.map.opengl.GLMapView;
import java.nio.file.*;
public final class GLSubjectFault {
  static final RuntimeException primary=new RuntimeException("owned GL subject fault");
  static native boolean marshal(GLLayer3 layer,RuntimeException pending);
  static native boolean lastPublished();
  static final class Subject implements Layer {
    final boolean fail;int names;
    Subject(boolean fail){this.fail=fail;}
    public String getName(){names++;if(fail)throw primary;return "owned subject";}
    public boolean isVisible(){return true;}
    public void setVisible(boolean visible){}
    public void addOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){}
    public void removeOnLayerVisibleChangedListener(OnLayerVisibleChangedListener listener){}
  }
  static final class Owned implements GLLayer3 {
    final boolean fail;final Subject subject;int calls;
    Owned(boolean fail,boolean nameFail){this.fail=fail;subject=new Subject(nameFail);}
    public Layer getSubject(){calls++;if(fail)throw primary;return subject;}
    public int getRenderPass(){return GLMapView.RENDER_PASS_SURFACE;}
    public void start(){}
    public void stop(){}
    public void release(){}
    public void draw(GLMapView view){throw new AssertionError("constructor drew");}
    public void draw(GLMapView view,int pass){throw new AssertionError("constructor drew");}
  }
  static void positive(){Owned layer=new Owned(false,false);if(!marshal(layer,null)||!lastPublished()||layer.calls!=1||layer.subject.names!=1)throw new AssertionError("fresh GL subject recovery failed");}
  public static void main(String[] args)throws Exception {
    for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);
    System.load(args[1]);positive();
    for(int mode=0;mode<3;mode++) {
      Owned layer=new Owned(mode==0,mode==1);
      try{marshal(layer,mode==2?primary:null);throw new AssertionError("missing GL subject exception");}
      catch(RuntimeException error){if(error!=primary)throw new AssertionError("GL primary replaced",error);}
      if(lastPublished())throw new AssertionError("failed GL peer published");
      if(layer.calls!=(mode==2?0:1))throw new AssertionError("callback dispatch differs");
      positive();System.out.println("GL_SUBJECT_FAILURE_PRIMARY_UNPUBLISHED_RECOVERED "+mode);
    }
    System.out.println("GL_SUBJECT_CONSTRUCTOR_CONTROLS_COMPLETED");
    System.out.println("PRIMARY_EXCEPTION_IDENTITY_PRESERVED");
    System.out.println("FRESH_CONSTRUCTOR_RECOVERY_COMPLETED");
  }
}
