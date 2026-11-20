// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
#include "interop/renderer/core/Interop.h"
static bool published=false;
extern "C" JNIEXPORT jboolean JNICALL Java_com_atakmap_map_layer_opengl_GLSubjectFault_marshal(
    JNIEnv *env,jclass,jobject layer,jthrowable pending)
{
    if(pending)env->Throw(pending);
    std::shared_ptr<TAK::Engine::Renderer::Core::GLLayer2> peer;
    const auto code=TAKEngineJNI::Interop::Renderer::Core::Interop_marshal(peer,*env,layer);
    published=bool(peer);
    return code==TAK::Engine::Util::TE_Ok&&peer ? JNI_TRUE:JNI_FALSE;
}
extern "C" JNIEXPORT jboolean JNICALL Java_com_atakmap_map_layer_opengl_GLSubjectFault_lastPublished(JNIEnv *,jclass)
{ return published ? JNI_TRUE:JNI_FALSE; }
