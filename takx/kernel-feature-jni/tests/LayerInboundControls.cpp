// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
#include <atomic>
#include "core/AbstractLayer.h"
#include "interop/core/Interop.h"
#include "common.h"
namespace {
 std::atomic<int> live(0);atakmap::core::Layer *original=nullptr;
 class OwnedLayer final:public atakmap::core::AbstractLayer {
  bool counted;
 public:
  explicit OwnedLayer(bool count=true):AbstractLayer("owned inbound layer"),counted(count){if(counted)++live;}
  ~OwnedLayer() NOTHROWS {if(counted)--live;}
 };
}
extern "C" JNIEXPORT jobject JNICALL Java_com_atakmap_map_layer_LayerInboundControls_create(JNIEnv *env,jclass,jboolean shared){
 TAKEngineJNI::Interop::Java::JNILocalRef result(*env,nullptr);original=new OwnedLayer();TAK::Engine::Util::TAKErr code;
 if(shared){std::shared_ptr<atakmap::core::Layer> value(original);code=TAKEngineJNI::Interop::Core::Interop_marshal(result,*env,value);}
 else{atakmap::core::LayerPtr value(original,[](const atakmap::core::Layer *p){delete p;});code=TAKEngineJNI::Interop::Core::Interop_marshal(result,*env,std::move(value));}
 if(env->ExceptionCheck()||ATAKMapEngineJNI_checkOrThrow(env,code))return nullptr;return result.release();
}
extern "C" JNIEXPORT jlongArray JNICALL Java_com_atakmap_map_layer_LayerInboundControls_convert(JNIEnv *env,jclass,jobject layer,jint loops){
 std::shared_ptr<atakmap::core::Layer> sentinel(new OwnedLayer(false)),result=sentinel;TAK::Engine::Util::TAKErr code=TAK::Engine::Util::TE_Err;
 if(loops<1||loops>1024)return nullptr;
 for(int i=0;i<loops;++i){code=TAKEngineJNI::Interop::Core::Interop_marshal(result,*env,layer);if(env->ExceptionCheck())return nullptr;if(code!=TAK::Engine::Util::TE_Ok)break;}
 jlong data[]={code,result==sentinel?1:0,result.get()==original?1:0};jlongArray value=env->NewLongArray(3);if(value)env->SetLongArrayRegion(value,0,3,data);return value;
}
extern "C" JNIEXPORT void JNICALL Java_com_atakmap_map_layer_LayerInboundControls_pending(JNIEnv *env,jclass,jobject layer,jthrowable primary){
 std::shared_ptr<atakmap::core::Layer> sentinel(new OwnedLayer(false)),result=sentinel;env->Throw(primary);
 const auto code=TAKEngineJNI::Interop::Core::Interop_marshal(result,*env,layer);jthrowable error=env->ExceptionOccurred();if(error)env->ExceptionClear();
 bool same=error&&env->IsSameObject(error,primary);if(error)env->DeleteLocalRef(error);
 if(code!=TAK::Engine::Util::TE_Err||result!=sentinel||!same){env->ThrowNew(env->FindClass("java/lang/AssertionError"),"pending conversion changed state");return;}
 env->Throw(primary);
}
extern "C" JNIEXPORT jint JNICALL Java_com_atakmap_map_layer_LayerInboundControls_alive(JNIEnv *,jclass){return live.load();}
