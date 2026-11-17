// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
#include <atomic>
#include <new>
#include <cstddef>
namespace { thread_local int allocationCountdown = -1; }
extern "C" void *__real__Znwm(std::size_t);
extern "C" void *__wrap__Znwm(std::size_t size) {
 if(allocationCountdown == 0) { allocationCountdown = -1; throw std::bad_alloc(); }
 if(allocationCountdown > 0) --allocationCountdown;
 return __real__Znwm(size);
}
#include "core/AbstractLayer.h"
#include "interop/core/Interop.h"
#include "common.h"
#include "interop/Pointer.h"
namespace {
 std::atomic<int> live(0);atakmap::core::Layer *original=nullptr;
 class OwnedLayer final:public atakmap::core::AbstractLayer {
  bool counted;
 public:
  explicit OwnedLayer(bool count=true):AbstractLayer("owned inbound layer"),counted(count){if(counted)++live;}
  ~OwnedLayer() NOTHROWS {if(counted)--live;}
 };
}
extern "C" JNIEXPORT jobject JNICALL Java_com_atakmap_map_layer_PointerLifetimeControls_create(JNIEnv *env,jclass,jboolean shared){
 TAKEngineJNI::Interop::Java::JNILocalRef result(*env,nullptr);original=new OwnedLayer();TAK::Engine::Util::TAKErr code;
 if(shared){std::shared_ptr<atakmap::core::Layer> value(original);code=TAKEngineJNI::Interop::Core::Interop_marshal(result,*env,value);}
 else{atakmap::core::LayerPtr value(original,[](const atakmap::core::Layer *p){delete p;});code=TAKEngineJNI::Interop::Core::Interop_marshal(result,*env,std::move(value));}
 if(env->ExceptionCheck()||ATAKMapEngineJNI_checkOrThrow(env,code))return nullptr;return result.release();
}
extern "C" JNIEXPORT jlongArray JNICALL Java_com_atakmap_map_layer_PointerLifetimeControls_inspect(JNIEnv *env,jclass,jobject pointer,jint action,jthrowable primary){
 std::shared_ptr<atakmap::core::Layer> sentinel(new OwnedLayer(false)),result=sentinel;long status=-1;
 if(primary)env->Throw(primary);
 switch(action){
 case 4: case 5: allocationCountdown=action-4;status=TAKEngineJNI::Interop::Pointer_makeShared<atakmap::core::Layer>(env,pointer)?1:0;allocationCountdown=-1;break;
 case 0:status=TAKEngineJNI::Interop::Pointer_get<atakmap::core::Layer>(*env,pointer)?1:0;break;
 case 1:status=TAKEngineJNI::Interop::Pointer_get<atakmap::core::Layer>(result,*env,pointer);break;
 case 2:status=TAKEngineJNI::Interop::Pointer_makeShared<atakmap::core::Layer>(env,pointer)?1:0;break;
 case 3:status=TAKEngineJNI::Interop::Pointer_isShared<atakmap::core::Layer>(env,pointer)?1:0;break;
 default:return nullptr;
 }
 if(primary){
  jthrowable error=env->ExceptionOccurred();if(error)env->ExceptionClear();bool same=error&&env->IsSameObject(error,primary);if(error)env->DeleteLocalRef(error);
  if(!same||result!=sentinel||status!=(action==1?TAK::Engine::Util::TE_Err:0)){env->ThrowNew(env->FindClass("java/lang/AssertionError"),"pending Pointer helper changed state");return nullptr;}
  env->Throw(primary);return nullptr;
 }
 if(env->ExceptionCheck())return nullptr;
 jlong data[]={status,result==sentinel?1:0,result.get()==original?1:0};jlongArray value=env->NewLongArray(3);if(value)env->SetLongArrayRegion(value,0,3,data);return value;
}
extern "C" JNIEXPORT jint JNICALL Java_com_atakmap_map_layer_PointerLifetimeControls_alive(JNIEnv *,jclass){return live.load();}
