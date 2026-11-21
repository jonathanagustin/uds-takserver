// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
package gov.tak.api.contour;

/** Project implementation of the callback consumed by pinned TAKX contour UI. */
@FunctionalInterface
public interface ContourFunction<T> {
    T apply(double elevationMeters, boolean isMajor);
}
