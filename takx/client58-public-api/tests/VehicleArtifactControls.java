// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
import gov.tak.api.cot.detail.VehicleModelDetailHandler;
import java.nio.file.Path;

public final class VehicleArtifactControls {
    public static void main(String[] args) throws Exception {
        Path expected = Path.of(args[0]).toRealPath();
        for (Class<?> type : new Class<?>[]{VehicleModelDetailHandler.class, VehicleModelDetailHandler.Interop.class}) {
            Path actual = Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath();
            if (!actual.equals(expected)) throw new AssertionError("vehicle API did not load from the packaged JAR");
        }
        VehicleModelControls.main(new String[0]);
        System.out.println("VEHICLE_METADATA_ARTIFACT_PASS classes=2");
    }
}
