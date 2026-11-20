// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
#include "interop/Pointer.h"
#include <feature/FeatureDataStore3.h>
extern "C" JNIEXPORT void JNICALL Java_com_atakmap_map_layer_feature_FeatureRegistrationFault_destructWithPending(
    JNIEnv *env,jclass,jobject pointer,jthrowable primary)
{
    env->Throw(primary);
    TAKEngineJNI::Interop::Pointer_destruct_iface<TAK::Engine::Feature::FeatureDataStore3>(env,pointer);
}
