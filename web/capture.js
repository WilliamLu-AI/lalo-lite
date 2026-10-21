class Capture extends AudioWorkletProcessor {
  constructor(){super();this.samples=[];this.position=0;this.frame=[];this.ratio=sampleRate/16000;}
  process(inputs){
    const input=inputs[0]?.[0];if(!input)return true;
    for(const x of input)this.samples.push(x);
    while(this.position+Math.max(1,this.ratio)<this.samples.length){
      const index=Math.floor(this.position),fraction=this.position-index;
      const value=this.samples[index]*(1-fraction)+this.samples[index+1]*fraction;
      this.frame.push(Math.max(-32768,Math.min(32767,Math.round(value*32768))));
      this.position+=this.ratio;
      if(this.frame.length===320){const pcm=new Int16Array(this.frame);this.port.postMessage(pcm.buffer,[pcm.buffer]);this.frame=[];}
    }
    const remove=Math.floor(this.position);this.samples.splice(0,remove);this.position-=remove;
    return true;
  }
}
registerProcessor('capture',Capture);
