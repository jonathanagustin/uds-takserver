package com.atakmap.map.layer;
import java.nio.file.*;
import java.util.*;
public final class PointerEmptyControls {
 static native long[] check();
 public static void main(String[] args)throws Exception {
  for(String line:Files.readAllLines(Path.of(args[0])))System.load(line.split(" ",2)[1]);
  System.load(args[1]);System.load(args[2]);
  Layer initialized=PointerLifetimeControls.create(true);((gov.tak.api.util.Disposable)initialized).dispose();
  System.out.println("EMPTY_SHARED_RESULT "+Arrays.toString(check()));
 }
}
