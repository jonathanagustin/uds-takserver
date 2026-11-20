// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
#include <atomic>
#include <cstddef>
#include <new>
#include "core/AbstractLayer.h"
#include "interop/core/Interop.h"
#include "common.h"
namespace {
 std::atomic<long> alive(0),created(0),destroyed(0),secondary(0);
 JNIEnv *ownerEnv=nullptr;jthrowable cleanupError=nullptr;atakmap::core::Layer *current=nullptr;std::shared_ptr<atakmap::core::Layer> held;long lastCode=0;bool published=false;
 alignas(std::max_align_t) unsigned char slot[8192];
 class OwnedLayer final:public atakmap::core::AbstractLayer {
 public:
  OwnedLayer():AbstractLayer("owned class-init layer"){++alive;++created;current=this;}
  ~OwnedLayer() NOTHROWS {current=nullptr;--alive;++destroyed;if(cleanupError){++secondary;ownerEnv->Throw(cleanupError);ownerEnv->DeleteGlobalRef(cleanupError);cleanupError=nullptr;}}
  static void *operator new(std::size_t size){if(size>sizeof(slot)||alive.load())throw std::bad_alloc();return slot;}
  static void operator delete(void *) NOTHROWS {}
 };
 jobject finish(JNIEnv *env,TAK::Engine::Util::TAKErr code,TAKEngineJNI::Interop::Java::JNILocalRef &value){
  lastCode=code;published=value.get()!=nullptr;
  if(env->ExceptionCheck())return nullptr;
  if(ATAKMapEngineJNI_checkOrThrow(env,code))return nullptr;
  return value.release();
 }
}
extern "C" JNIEXPORT jobject JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_createShared(JNIEnv *env,jclass){
 ownerEnv=env;std::shared_ptr<atakmap::core::Layer> layer(new OwnedLayer());TAKEngineJNI::Interop::Java::JNILocalRef value(*env,nullptr);
 const auto code=TAKEngineJNI::Interop::Core::Interop_marshal(value,*env,layer);return finish(env,code,value);
}
extern "C" JNIEXPORT jobject JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_createUnique(JNIEnv *env,jclass){
 ownerEnv=env;atakmap::core::LayerPtr layer(new OwnedLayer(),[](const atakmap::core::Layer *value){delete value;});TAKEngineJNI::Interop::Java::JNILocalRef value(*env,nullptr);
 const auto code=TAKEngineJNI::Interop::Core::Interop_marshal(value,*env,std::move(layer));return finish(env,code,value);
}
extern "C" JNIEXPORT jlongArray JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_counts(JNIEnv *env,jclass){
 jlong data[]={alive.load(),created.load(),destroyed.load(),published?1:0,lastCode,secondary.load()};jlongArray result=env->NewLongArray(6);if(result)env->SetLongArrayRegion(result,0,6,data);return result;
}

extern "C" JNIEXPORT void JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_holdNative(JNIEnv *env,jclass){ownerEnv=env;held=std::shared_ptr<atakmap::core::Layer>(new OwnedLayer());}
extern "C" JNIEXPORT void JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_releaseHeld(JNIEnv *,jclass){held.reset();}
extern "C" JNIEXPORT jobject JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_wrapHeld(JNIEnv *env,jclass){TAKEngineJNI::Interop::Java::JNILocalRef value(*env,nullptr);const auto code=TAKEngineJNI::Interop::Core::Interop_marshal(value,*env,held);return finish(env,code,value);}
extern "C" JNIEXPORT jobject JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_reference(JNIEnv *env,jclass){
 if(!current)return nullptr;TAKEngineJNI::Interop::Java::JNILocalRef value(*env,nullptr);const auto code=TAKEngineJNI::Interop::Core::Interop_marshal(value,*env,*current);lastCode=code;published=value.get()!=nullptr;if(env->ExceptionCheck()||code!=TAK::Engine::Util::TE_Ok)return nullptr;return value.release();
}
extern "C" JNIEXPORT void JNICALL Java_com_atakmap_map_layer_LayerClassInitControls_00024Scenario_setCleanupError(JNIEnv *env,jclass,jthrowable value){ownerEnv=env;if(cleanupError)env->DeleteGlobalRef(cleanupError);cleanupError=value?(jthrowable)env->NewGlobalRef(value):nullptr;}
