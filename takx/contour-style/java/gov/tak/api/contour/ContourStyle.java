// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package gov.tak.api.contour;

import com.atakmap.coremap.conversions.Span;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.function.BiFunction;
import java.util.function.IntConsumer;

/**
 * Observable project contour styling. No native layer or private SDK identity is
 * supplied by this model. Setters notify a listener snapshot synchronously after
 * releasing the model monitor; callback objects remain caller-owned. Every
 * successful setter notifies, including assignments of an existing value.
 * Concurrent setters may dispatch concurrently. Listeners must synchronize
 * their own shared state; this class does not dispatch onto a UI thread.
 */
public final class ContourStyle {
    public static final int PROP_CONTOUR_COLOR_MAPPER = 1;
    public static final int PROP_CONTOUR_STROKE_THICKNESS_MAPPER = 2;
    public static final int PROP_MAJOR_LINES_ENABLED = 3;
    public static final int PROP_MINOR_LINES_ENABLED = 4;
    public static final int PROP_LINES_OUTLINED = 5;
    public static final int PROP_LABEL_ENABLED = 6;
    public static final int PROP_LABEL_SIZE_MAPPER = 7;
    public static final int PROP_LABEL_UNITS = 8;
    public static final int PROP_LABEL_TEXT_MAPPER = 9;
    private static final int MAX_LISTENERS = 256;

    private final List<IntConsumer> listeners = new ArrayList<>();
    private ContourFunction<Integer> colorMapper;
    private ContourFunction<Float> thicknessMapper;
    private ContourFunction<Float> labelSizeMapper;
    private BiFunction<Double, Span, String> labelTextMapper = ContourStyle::formatLabel;
    private Span labelUnits;
    private boolean majors, minors, outlined, labels;

    public ContourStyle() {
        this(0xFFFFFFFF, 0xFFAAAAAA, 2, 1, true, true, 20, true, Span.METER);
    }

    /** Explicit project defaults follow the pinned ContourListPanel construction. */
    public ContourStyle(int majorColor, int minorColor, float majorWidth, float minorWidth,
            boolean majorLines, boolean minorLines, float labelSize, boolean labelsEnabled, Span units) {
        positive(majorWidth, 64, "major stroke width");
        positive(minorWidth, 64, "minor stroke width");
        positive(labelSize, 256, "label size");
        colorMapper = (elevation, major) -> major ? majorColor : minorColor;
        thicknessMapper = (elevation, major) -> major ? majorWidth : minorWidth;
        labelSizeMapper = (elevation, major) -> labelSize;
        majors = majorLines; minors = minorLines; labels = labelsEnabled;
        labelUnits = Objects.requireNonNull(units, "units");
    }

    /** Copies current values/callback identities, with an independent listener registry. */
    public synchronized ContourStyle copy() {
        ContourStyle value = new ContourStyle();
        value.colorMapper = colorMapper; value.thicknessMapper = thicknessMapper;
        value.labelSizeMapper = labelSizeMapper; value.labelTextMapper = labelTextMapper;
        value.labelUnits = labelUnits; value.majors = majors; value.minors = minors;
        value.outlined = outlined; value.labels = labels;
        return value;
    }

    public synchronized ContourFunction<Integer> getContourColorMapper() { return colorMapper; }
    public synchronized ContourFunction<Float> getContourStrokeThicknessMapper() { return thicknessMapper; }
    public synchronized ContourFunction<Float> getContourLabelSizeMapper() { return labelSizeMapper; }
    public synchronized BiFunction<Double, Span, String> getContourLabelTextMapper() { return labelTextMapper; }
    public synchronized Span getLabelUnits() { return labelUnits; }
    public synchronized boolean isMajorLinesEnabled() { return majors; }
    public synchronized boolean isMinorLinesEnabled() { return minors; }
    public synchronized boolean isLinesOutlined() { return outlined; }
    public synchronized boolean isLabelsEnabled() { return labels; }

    public void setContourColors(int major, int minor) {
        setContourLineColorMapper((elevation, isMajor) -> isMajor ? major : minor);
    }
    public void setContourLineColorMapper(ContourFunction<Integer> mapper) {
        Objects.requireNonNull(mapper, "mapper");
        update(PROP_CONTOUR_COLOR_MAPPER, () -> colorMapper = mapper);
    }
    public void setContourStrokeThicknessMapper(ContourFunction<Float> mapper) {
        Objects.requireNonNull(mapper, "mapper");
        update(PROP_CONTOUR_STROKE_THICKNESS_MAPPER, () -> thicknessMapper = mapper);
    }
    public void setContourLabelSizeMapper(ContourFunction<Float> mapper) {
        Objects.requireNonNull(mapper, "mapper");
        update(PROP_LABEL_SIZE_MAPPER, () -> labelSizeMapper = mapper);
    }
    public void setContourLabelTextMapper(BiFunction<Double, Span, String> mapper) {
        Objects.requireNonNull(mapper, "mapper");
        update(PROP_LABEL_TEXT_MAPPER, () -> labelTextMapper = mapper);
    }
    public void setLabelUnits(Span units) {
        Objects.requireNonNull(units, "units");
        update(PROP_LABEL_UNITS, () -> labelUnits = units);
    }
    public void setMajorLinesEnabled(boolean enabled) { update(PROP_MAJOR_LINES_ENABLED, () -> majors = enabled); }
    public void setMinorLinesEnabled(boolean enabled) { update(PROP_MINOR_LINES_ENABLED, () -> minors = enabled); }
    public void setLinesOutlined(boolean enabled) { update(PROP_LINES_OUTLINED, () -> outlined = enabled); }
    public void setLabelsEnabled(boolean enabled) { update(PROP_LABEL_ENABLED, () -> labels = enabled); }

    /** Identity-based registration, insertion-order dispatch, duplicate registration ignored. */
    public synchronized void addChangeListener(IntConsumer listener) {
        Objects.requireNonNull(listener, "listener");
        for (IntConsumer existing : listeners) if (existing == listener) return;
        if (listeners.size() == MAX_LISTENERS) throw new IllegalStateException("contour style listener limit exceeded");
        listeners.add(listener);
    }
    public synchronized void removeChangeListener(IntConsumer listener) {
        Objects.requireNonNull(listener, "listener");
        listeners.removeIf(existing -> existing == listener);
    }

    private void update(int property, Runnable mutation) {
        List<IntConsumer> snapshot;
        synchronized (this) {
            mutation.run();
            snapshot = List.copyOf(listeners);
        }
        Throwable first = null;
        for (IntConsumer listener : snapshot) {
            try { listener.accept(property); }
            catch (RuntimeException | Error error) {
                if (first == null) first = error;
                else if (error != first) first.addSuppressed(error);
            }
        }
        if (first instanceof RuntimeException error) throw error;
        if (first instanceof Error error) throw error;
    }

    private static void positive(float value, float maximum, String name) {
        if (!Float.isFinite(value) || value <= 0 || value > maximum)
            throw new IllegalArgumentException("bounded positive " + name + " required");
    }
    private static String formatLabel(double meters, Span units) {
        if (!Double.isFinite(meters)) throw new IllegalArgumentException("finite elevation required");
        Objects.requireNonNull(units, "units");
        double factor = switch (units) {
            case METER -> 1;
            case KILOMETER -> 1000;
            case FOOT -> .3048;
            case YARD -> .9144;
            case MILE -> 1609.344;
            case NAUTICALMILE -> 1852;
        };
        return String.format(Locale.ROOT, "%.0f %s", meters / factor, units.getAbbrev());
    }
}
