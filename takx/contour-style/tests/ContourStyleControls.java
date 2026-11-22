// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package gov.tak.api.contour;

import com.atakmap.coremap.conversions.Span;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.IntConsumer;

public final class ContourStyleControls {
    private static int checks;
    private static void check(boolean value, String reason) {
        if (!value) throw new AssertionError(reason);
        checks++;
    }
    private static void rejects(Class<? extends Throwable> type, Runnable action) {
        try { action.run(); }
        catch (Throwable error) { check(type.isInstance(error), "wrong error: " + error); return; }
        throw new AssertionError("missing " + type.getName());
    }
    public static void main(String[] args) throws Exception {
        for (Class<?> api : List.of(ContourStyle.class, ContourFunction.class))
            check(java.nio.file.Path.of(api.getProtectionDomain().getCodeSource().getLocation().toURI())
                    .toRealPath().equals(java.nio.file.Path.of(args[0]).toRealPath()), "API loaded from packaged artifact");
        ContourStyle style = new ContourStyle();
        check(style.getContourColorMapper().apply(0, true) == 0xFFFFFFFF, "major ARGB");
        check(style.getContourColorMapper().apply(0, false) == 0xFFAAAAAA, "minor ARGB");
        check(style.getContourStrokeThicknessMapper().apply(0, true) == 2f, "major width");
        check(style.getContourStrokeThicknessMapper().apply(0, false) == 1f, "minor width");
        check(style.getContourLabelSizeMapper().apply(0, true) == 20f, "label size");
        check(style.getLabelUnits() == Span.METER, "default units");
        check(style.isMajorLinesEnabled() && style.isMinorLinesEnabled() && style.isLabelsEnabled(), "default visibility");
        check(!style.isLinesOutlined(), "default outline");
        for (float bad : new float[]{0, -1, Float.NaN, Float.POSITIVE_INFINITY, 65})
            rejects(IllegalArgumentException.class, () -> new ContourStyle(0, 0, bad, 1, true, true, 20, true, Span.METER));
        rejects(IllegalArgumentException.class, () -> new ContourStyle(0, 0, 1, 1, true, true, 257, true, Span.METER));
        rejects(NullPointerException.class, () -> style.setContourLineColorMapper(null));
        rejects(NullPointerException.class, () -> style.setContourStrokeThicknessMapper(null));
        rejects(NullPointerException.class, () -> style.setContourLabelSizeMapper(null));
        rejects(NullPointerException.class, () -> style.setContourLabelTextMapper(null));
        rejects(NullPointerException.class, () -> style.setLabelUnits(null));

        List<Integer> events = new ArrayList<>();
        IntConsumer record = events::add;
        style.addChangeListener(record); style.addChangeListener(record);
        style.setContourColors(0x12345678, 0x9ABCDEF0);
        check(events.equals(List.of(ContourStyle.PROP_CONTOUR_COLOR_MAPPER)), "one duplicate-free notification");
        check(style.getContourColorMapper().apply(-50, true) == 0x12345678, "signed/alpha major color");
        check(style.getContourColorMapper().apply(500, false) == 0x9ABCDEF0, "signed/alpha minor color");
        ContourFunction<Integer> gradient = (elevation, major) -> (int)elevation + (major ? 100 : 0);
        style.setContourLineColorMapper(gradient);
        check(style.getContourColorMapper() == gradient, "mapper identity retained");
        check(style.getContourColorMapper().apply(12, true) == 112, "actual callback arguments");
        style.setContourStrokeThicknessMapper((elevation, major) -> major ? 4f : 3f);
        style.setContourLabelSizeMapper((elevation, major) -> major ? 24f : 18f);
        style.setLabelUnits(Span.FOOT);
        style.setMajorLinesEnabled(false); style.setMinorLinesEnabled(false);
        style.setLinesOutlined(true); style.setLabelsEnabled(false);
        check(!style.isMajorLinesEnabled() && !style.isMinorLinesEnabled() && !style.isLabelsEnabled(), "changed visibility");
        check(style.isLinesOutlined(), "changed outline");
        check(style.getContourStrokeThicknessMapper().apply(0, false) == 3f, "changed width");
        check(style.getContourLabelSizeMapper().apply(0, true) == 24f, "changed label size");
        check(events.equals(List.of(ContourStyle.PROP_CONTOUR_COLOR_MAPPER, ContourStyle.PROP_CONTOUR_COLOR_MAPPER,
                ContourStyle.PROP_CONTOUR_STROKE_THICKNESS_MAPPER, ContourStyle.PROP_LABEL_SIZE_MAPPER,
                ContourStyle.PROP_LABEL_UNITS, ContourStyle.PROP_MAJOR_LINES_ENABLED, ContourStyle.PROP_MINOR_LINES_ENABLED,
                ContourStyle.PROP_LINES_OUTLINED, ContourStyle.PROP_LABEL_ENABLED)), "property notifications match setters");
        style.removeChangeListener(record); style.removeChangeListener(record);
        style.setLabelsEnabled(true);
        check(events.size() == 9, "listener removal");

        Locale previous = Locale.getDefault();
        try {
            Locale.setDefault(Locale.GERMANY);
            double[] meters = {1000, 1, 1609.344, .9144, .3048, 1852};
            String[] labels = {"1 km", "1 m", "1 mi", "1 yd", "1 ft", "1 NM"};
            int index = 0;
            for (Span unit : Span.values())
                check(style.getContourLabelTextMapper().apply(meters[index], unit).equals(labels[index++]), "actual Span conversion " + unit);
            check(style.getContourLabelTextMapper().apply(-.3048, Span.FOOT).equals("-1 ft"), "negative elevation label");
            rejects(IllegalArgumentException.class, () -> style.getContourLabelTextMapper().apply(Double.NaN, Span.METER));
        } finally { Locale.setDefault(previous); }
        style.setContourLabelTextMapper((meters, unit) -> unit.getAbbrev() + ":" + meters);
        check(style.getContourLabelTextMapper().apply(3d, Span.YARD).equals("yd:3.0"), "custom label callback");
        RuntimeException callbackError = new RuntimeException("owned mapper failure");
        style.setContourLineColorMapper((meters, major) -> { throw callbackError; });
        try { style.getContourColorMapper().apply(0, true); throw new AssertionError("callback error missing"); }
        catch (RuntimeException error) { check(error == callbackError, "mapper exception identity"); }

        ContourStyle copy = style.copy();
        check(copy.getContourColorMapper() == style.getContourColorMapper(), "copy retains callback identity");
        check(copy.getLabelUnits() == Span.FOOT && copy.isLinesOutlined(), "copy preserves current values");
        AtomicInteger originalEvents = new AtomicInteger(); style.addChangeListener(p -> originalEvents.incrementAndGet());
        copy.setLabelUnits(Span.YARD);
        check(style.getLabelUnits() == Span.FOOT && copy.getLabelUnits() == Span.YARD, "copy state independent");
        check(originalEvents.get() == 0, "copy listener registry independent");

        ContourStyle faults = new ContourStyle();
        RuntimeException primary = new RuntimeException("first listener");
        Error secondary = new AssertionError("second listener");
        AtomicInteger completed = new AtomicInteger();
        faults.addChangeListener(p -> { throw primary; });
        faults.addChangeListener(p -> { throw secondary; });
        faults.addChangeListener(p -> completed.incrementAndGet());
        try { faults.setLabelsEnabled(false); throw new AssertionError("listener error missing"); }
        catch (RuntimeException error) { check(error == primary, "primary listener identity"); }
        check(Arrays.equals(primary.getSuppressed(), new Throwable[]{secondary}), "secondary listener preserved");
        check(completed.get() == 1 && !faults.isLabelsEnabled(), "dispatch completes with committed state");

        ContourStyle unlocked = new ContourStyle();
        unlocked.addChangeListener(p -> {
            CountDownLatch read = new CountDownLatch(1);
            Thread observer = new Thread(() -> { unlocked.getLabelUnits(); read.countDown(); });
            observer.start();
            try {
                if (!read.await(5, TimeUnit.SECONDS)) throw new AssertionError("notification held model monitor");
                observer.join(5000);
            } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
        });
        unlocked.setLabelUnits(Span.FOOT);
        check(unlocked.getLabelUnits() == Span.FOOT, "notifications execute outside model monitor");

        ContourStyle bounded = new ContourStyle();
        IntConsumer[] registered = new IntConsumer[256]; int[] received = new int[256];
        for (int i = 0; i < registered.length; i++) { int slot = i; registered[i] = p -> received[slot]++; bounded.addChangeListener(registered[i]); }
        rejects(IllegalStateException.class, () -> bounded.addChangeListener(p -> { throw new AssertionError("not registered"); }));
        bounded.setLinesOutlined(true);
        for (int count : received) check(count == 1, "bounded listener dispatch");
        bounded.removeChangeListener(registered[0]); bounded.addChangeListener(p -> completed.incrementAndGet());
        rejects(NullPointerException.class, () -> bounded.addChangeListener(null));

        ContourStyle concurrent = new ContourStyle(); AtomicReference<Throwable> failure = new AtomicReference<>();
        CountDownLatch start = new CountDownLatch(1); List<Thread> workers = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            int worker = i;
            Thread thread = new Thread(() -> {
                try {
                    start.await();
                    for (int step = 0; step < 1000; step++) {
                        concurrent.setLabelUnits(worker == 0 ? Span.METER : Span.FOOT);
                        concurrent.setContourColors(step, -step);
                        if (concurrent.copy().getLabelUnits() == null || concurrent.getContourColorMapper() == null)
                            throw new AssertionError("unpublished state");
                    }
                } catch (Throwable error) { failure.compareAndSet(null, error); }
            }); workers.add(thread); thread.start();
        }
        start.countDown();
        for (Thread worker : workers) { worker.join(10000); check(!worker.isAlive(), "concurrent worker retired"); }
        check(failure.get() == null, "concurrent state remains valid");
        System.out.println("contour-style-controls passed checks=" + checks);
    }
}
